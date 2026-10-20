"""依据用户书面确认生成可用于正式评测的版本化 Gold。"""

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

import structlog
from pydantic import Field

from smart_compliance.domain.models import GoldIssue, StrictModel, Verdict
from smart_compliance.evaluation.gold_loader import load_gold, save_gold_candidates, validate_gold_file


class ReferenceDecision(StrictModel):
    """现有参考答案候选的整体审核决定。"""

    source: str
    decision: str
    candidate_count: int = Field(ge=1)


class ArbitrationDecision(StrictModel):
    """参考答案以外问题的人工仲裁决定。"""

    sample_id: str = Field(pattern=r"^\d{3}$")
    decision: str
    issue_type: Verdict
    clause_code: str
    issue_title: str
    procurement_text: str
    contract_text: str
    difference_summary: str
    notes: str


class GoldReviewDecision(StrictModel):
    """用户确认的完整 Gold 审核决定。"""

    version: str
    reviewer: str
    reviewed_at: datetime
    authority_basis: str
    reference_candidates: ReferenceDecision
    arbitration_items: list[ArbitrationDecision]


def main() -> None:
    """生成 verified-v1 Gold、可读审核表与完整性清单。"""
    project_root = Path(__file__).resolve().parents[1]
    decision_path = project_root / "data" / "annotations" / "gold-review-decision-v1.json"
    decision = GoldReviewDecision.model_validate_json(decision_path.read_text(encoding="utf-8"))
    source_path = project_root / decision.reference_candidates.source
    source_issues = load_gold(source_path)
    if len(source_issues) != decision.reference_candidates.candidate_count:
        raise ValueError("审核决定中的候选数量与视觉候选文件不一致")
    confirmed = [_confirm_issue(issue, decision) for issue in source_issues]
    confirmed.extend(_arbitration_issue(item, decision, project_root) for item in decision.arbitration_items)
    output_dir = project_root / "data" / "gold"
    gold_path = save_gold_candidates(confirmed, output_dir / "verified-v1.json")
    review_path = _save_review_sheet(confirmed, output_dir / "verified-v1-review.csv")
    manifest_path = _save_manifest(confirmed, decision, output_dir / "verified-v1-manifest.json")
    report = validate_gold_file(gold_path, manifest_path)
    if not report.ready_for_metrics:
        raise ValueError("生成的 Gold 未通过正式指标门禁: " + "; ".join(report.warnings))
    structlog.get_logger().info(
        "verified_gold_built",
        candidates=len(confirmed),
        gold=str(gold_path),
        review=str(review_path),
        manifest=str(manifest_path),
    )


def _confirm_issue(issue: GoldIssue, decision: GoldReviewDecision) -> GoldIssue:
    return issue.model_copy(
        update={
            "human_verified": True,
            "reviewer": decision.reviewer,
            "reviewed_at": decision.reviewed_at,
            "notes": "用户确认参考答案视觉候选成立。",
        }
    )


def _arbitration_issue(
    item: ArbitrationDecision,
    decision: GoldReviewDecision,
    project_root: Path,
) -> GoldIssue:
    sample_dir = project_root / "data" / "raw" / "智审合规助手数据集" / "数据集_无水印" / item.sample_id
    procurement_path = next(sample_dir.glob("*_采购结果.pdf"))
    contract_path = next(sample_dir.glob("*_采购合同.pdf"))
    combined_hash = hashlib.sha256((_sha256(procurement_path) + _sha256(contract_path)).encode()).hexdigest()
    candidate_raw = f"{item.sample_id}|{item.issue_type}|{item.clause_code}|{item.difference_summary}"
    evidence = "\n".join(
        [
            f"问题：{item.issue_title}",
            f"采购结果：{item.procurement_text}",
            f"合同：{item.contract_text}",
            f"差异说明：{item.difference_summary}",
        ]
    )
    return GoldIssue(
        sample_id=item.sample_id,
        issue_type=item.issue_type,
        clause_code=item.clause_code,
        evidence=evidence,
        source_file=f"{procurement_path.name} + {contract_path.name}",
        candidate_id=hashlib.sha256(candidate_raw.encode()).hexdigest()[:16],
        reference_sha256=combined_hash,
        parsing_warnings=["该项不在参考答案页面中，由模型提出后经用户人工仲裁确认计入 Gold"],
        human_verified=True,
        reviewer=decision.reviewer,
        reviewed_at=decision.reviewed_at,
        notes=item.notes,
    )


def _save_review_sheet(issues: list[GoldIssue], output_path: Path) -> Path:
    with output_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "sample_id",
                "candidate_id",
                "issue_type",
                "clause_code",
                "evidence",
                "source_file",
                "reviewer",
                "reviewed_at",
                "notes",
                "human_verified",
            ]
        )
        for issue in issues:
            writer.writerow(
                [
                    issue.sample_id,
                    issue.candidate_id,
                    issue.issue_type.value,
                    issue.clause_code,
                    issue.evidence,
                    issue.source_file,
                    issue.reviewer,
                    issue.reviewed_at.isoformat() if issue.reviewed_at else "",
                    issue.notes,
                    "true" if issue.human_verified else "false",
                ]
            )
    return output_path


def _save_manifest(issues: list[GoldIssue], decision: GoldReviewDecision, output_path: Path) -> Path:
    counts = {sample_id: sum(issue.sample_id == sample_id for issue in issues) for sample_id in _sample_ids()}
    payload = {
        "version": "verified-v1",
        "human_verified": True,
        "reviewer": decision.reviewer,
        "reviewed_at": decision.reviewed_at.isoformat(),
        "authority_basis": decision.authority_basis,
        "candidate_count": len(issues),
        "zero_issue_samples": [sample_id for sample_id, count in counts.items() if count == 0],
        "samples": [
            {
                "sample_id": sample_id,
                "candidate_count": count,
            }
            for sample_id, count in counts.items()
        ],
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def _sample_ids() -> list[str]:
    return [f"{number:03d}" for number in range(1, 21)]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
