"""从页面图像视觉转录稿生成可读的 Gold 候选审核材料。"""

import csv
import hashlib
import json
from datetime import date
from pathlib import Path

from pydantic import Field

from smart_compliance.domain.models import GoldIssue, StrictModel, Verdict
from smart_compliance.domain.taxonomy import CLAUSE_BY_CODE
from smart_compliance.evaluation.gold_loader import save_gold_candidates

VISUAL_WARNING = "PDF 文本层存在跨列交错；本候选由页面图像视觉转录生成，尚待人工复核"


class VisualIssue(StrictModel):
    """页面图像中逐项转录的参考答案差异。"""

    reference_item_no: int = Field(ge=1)
    candidate_id: str = Field(min_length=16, max_length=16)
    issue_type: Verdict
    clause_code: str
    issue_title: str
    location: str = ""
    procurement_text: str
    contract_text: str
    difference_summary: str


class VisualSample(StrictModel):
    """一份参考答案页面的视觉转录。"""

    sample_id: str = Field(pattern=r"^\d{3}$")
    project_name: str
    supplier: str
    reference_file: str
    source_page: int = Field(default=1, ge=1)
    page_image: str
    reference_issue_count: int = Field(ge=0)
    issues: list[VisualIssue]


class ArbitrationItem(StrictModel):
    """模型提出、但参考答案未列出的待人工仲裁项。"""

    sample_id: str = Field(pattern=r"^\d{3}$")
    source: str
    model_name: str
    issue_type: Verdict
    clause_code: str
    issue_title: str
    procurement_text: str
    contract_text: str
    model_reason: str
    reference_answer_status: str


class VisualTranscription(StrictModel):
    """视觉转录数据集。"""

    version: str
    transcription_date: date
    recognition_method: str
    human_verified: bool = False
    samples: list[VisualSample]
    arbitration_items: list[ArbitrationItem] = Field(default_factory=list)


def load_visual_transcription(path: Path) -> VisualTranscription:
    """读取并校验页面图像视觉转录稿。

    Args:
        path: 视觉转录 JSON 路径。

    Returns:
        已通过结构校验的视觉转录对象。
    """
    return VisualTranscription.model_validate_json(path.read_text(encoding="utf-8"))


def validate_visual_transcription(transcription: VisualTranscription) -> None:
    """验证样本覆盖、数量、候选编号和条款代码。

    Args:
        transcription: 待验证的视觉转录对象。

    Raises:
        ValueError: 视觉转录与参考答案覆盖规则不一致。
    """
    sample_ids = [sample.sample_id for sample in transcription.samples]
    expected_ids = [f"{number:03d}" for number in range(1, 21)]
    if sample_ids != expected_ids:
        raise ValueError("视觉转录必须按顺序完整覆盖 001-020")
    issues = [issue for sample in transcription.samples for issue in sample.issues]
    if len(issues) != 30:
        raise ValueError(f"视觉转录应包含 30 条参考差异，当前为 {len(issues)} 条")
    for sample in transcription.samples:
        if sample.reference_issue_count != len(sample.issues):
            raise ValueError(f"样本 {sample.sample_id} 的参考差异数量与明细不一致")
    candidate_ids = [issue.candidate_id for issue in issues]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("视觉转录中存在重复 candidate_id")
    unknown_codes = sorted({issue.clause_code for issue in issues if issue.clause_code not in CLAUSE_BY_CODE})
    if unknown_codes:
        raise ValueError("视觉转录中存在未知条款代码: " + ", ".join(unknown_codes))
    if transcription.human_verified:
        raise ValueError("AI 辅助视觉转录不能直接标记为已人工核验")


def build_visual_gold_artifacts(transcription_path: Path, project_root: Path, output_dir: Path) -> list[Path]:
    """生成视觉版候选 JSON、人工审核 CSV、仲裁表和完整性清单。

    Args:
        transcription_path: 视觉转录 JSON 路径。
        project_root: 项目根目录。
        output_dir: 输出目录。

    Returns:
        生成的文件路径列表。
    """
    transcription = load_visual_transcription(transcription_path)
    validate_visual_transcription(transcription)
    output_dir.mkdir(parents=True, exist_ok=True)
    issues = _to_gold_issues(transcription, project_root)
    candidates_path = save_gold_candidates(issues, output_dir / "visual-candidates.json")
    review_path = _save_review_sheet(transcription, project_root, output_dir / "reference-review-sheet.csv")
    arbitration_path = _save_arbitration_sheet(transcription, output_dir / "model-arbitration.csv")
    manifest_path = _save_manifest(transcription, project_root, output_dir / "visual-manifest.json")
    return [candidates_path, review_path, arbitration_path, manifest_path]


def _to_gold_issues(transcription: VisualTranscription, project_root: Path) -> list[GoldIssue]:
    issues: list[GoldIssue] = []
    for sample in transcription.samples:
        reference_path = _reference_path(project_root, sample)
        reference_sha256 = _sha256(reference_path)
        for issue in sample.issues:
            issues.append(
                GoldIssue(
                    sample_id=sample.sample_id,
                    issue_type=issue.issue_type,
                    clause_code=issue.clause_code,
                    evidence=_evidence(issue),
                    source_file=sample.reference_file,
                    candidate_id=issue.candidate_id,
                    reference_sha256=reference_sha256,
                    parsing_warnings=[VISUAL_WARNING],
                    human_verified=False,
                )
            )
    return issues


def _save_review_sheet(transcription: VisualTranscription, project_root: Path, output_path: Path) -> Path:
    headers = [
        "样本编号",
        "项目名称",
        "供应商",
        "参考答案序号",
        "候选编号",
        "问题类型",
        "条款代码",
        "条款名称",
        "问题标题",
        "位置",
        "采购结果原意",
        "合同原意",
        "差异说明",
        "来源PDF",
        "来源页码",
        "页面截图",
        "识别方式",
        "审核动作",
        "审核人",
        "审核日期",
        "审核备注",
        "人工核验",
    ]
    with output_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        for sample in transcription.samples:
            reference_path = _reference_path(project_root, sample)
            for issue in sample.issues:
                writer.writerow(
                    [
                        sample.sample_id,
                        sample.project_name,
                        sample.supplier,
                        issue.reference_item_no,
                        issue.candidate_id,
                        "缺失" if issue.issue_type == Verdict.MISSING else "不一致",
                        issue.clause_code,
                        CLAUSE_BY_CODE[issue.clause_code].name,
                        issue.issue_title,
                        issue.location,
                        issue.procurement_text,
                        issue.contract_text,
                        issue.difference_summary,
                        str(reference_path.relative_to(project_root)),
                        sample.source_page,
                        sample.page_image,
                        transcription.recognition_method,
                        "待确认（确认/修改/删除/补充）",
                        "",
                        "",
                        "",
                        "false",
                    ]
                )
    return output_path


def _save_arbitration_sheet(transcription: VisualTranscription, output_path: Path) -> Path:
    headers = [
        "样本编号",
        "来源",
        "模型",
        "问题类型",
        "条款代码",
        "问题标题",
        "采购结果原意",
        "合同原意",
        "模型理由",
        "参考答案状态",
        "人工决定",
        "审核人",
        "审核日期",
        "审核备注",
    ]
    with output_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        for item in transcription.arbitration_items:
            writer.writerow(
                [
                    item.sample_id,
                    item.source,
                    item.model_name,
                    "缺失" if item.issue_type == Verdict.MISSING else "不一致",
                    item.clause_code,
                    item.issue_title,
                    item.procurement_text,
                    item.contract_text,
                    item.model_reason,
                    item.reference_answer_status,
                    "待决定（成立/不成立/需补证据）",
                    "",
                    "",
                    "",
                ]
            )
    return output_path


def _save_manifest(transcription: VisualTranscription, project_root: Path, output_path: Path) -> Path:
    samples: list[dict[str, object]] = []
    for sample in transcription.samples:
        reference_path = _reference_path(project_root, sample)
        page_image_path = project_root / sample.page_image
        samples.append(
            {
                "sample_id": sample.sample_id,
                "reference_file": str(reference_path.relative_to(project_root)),
                "reference_sha256": _sha256(reference_path),
                "source_page": sample.source_page,
                "page_image": sample.page_image,
                "page_image_sha256": _sha256(page_image_path),
                "candidate_count": len(sample.issues),
            }
        )
    payload = {
        "version": transcription.version,
        "recognition_method": transcription.recognition_method,
        "human_verified": False,
        "candidate_count": sum(len(sample.issues) for sample in transcription.samples),
        "zero_issue_samples": [sample.sample_id for sample in transcription.samples if not sample.issues],
        "arbitration_item_count": len(transcription.arbitration_items),
        "samples": samples,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def _reference_path(project_root: Path, sample: VisualSample) -> Path:
    path = (
        project_root
        / "data"
        / "raw"
        / "智审合规助手数据集"
        / "数据集_无水印"
        / sample.sample_id
        / sample.reference_file
    )
    if not path.is_file():
        raise FileNotFoundError(f"找不到参考答案 PDF: {path}")
    return path


def _evidence(issue: VisualIssue) -> str:
    fields = [f"问题：{issue.issue_title}"]
    if issue.location:
        fields.append(f"位置：{issue.location}")
    fields.extend(
        [
            f"采购结果：{issue.procurement_text}",
            f"合同：{issue.contract_text}",
            f"差异说明：{issue.difference_summary}",
        ]
    )
    return "\n".join(fields)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()
