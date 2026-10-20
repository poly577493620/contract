"""从训练集参考答案构建待人工核验的 Gold 候选与审核材料。"""

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast

from pydantic import TypeAdapter

from smart_compliance.domain.models import DocumentRole, GoldIssue, Verdict
from smart_compliance.domain.taxonomy import CLAUSE_BY_CODE, detect_clause
from smart_compliance.evaluation.data_split import validate_gold_sample
from smart_compliance.evaluation.models import GoldValidationReport
from smart_compliance.parsing.pdf_parser import PdfParser

SAMPLE_PATTERN = re.compile(r"(?:^|[-_])(\d{3})(?:[-_]|\.)")
ENTRY_TOKENS = (
    "价格",
    "小计",
    "数量",
    "服务",
    "合同",
    "交货",
    "交付",
    "付款",
    "税率",
    "质保",
    "质量",
    "商务条款",
    "违约",
)
ENTRY_START_HINTS = (
    "价格偏差",
    "小计计算错误",
    "数量差异",
    "数量差",
    "细数差异",
    "服务范围不一致",
    "服务范围",
    "服务期限不一致",
    "合同有效期",
    "交货/交付时间",
    "付款方式",
    "式付款方",
    "税率说明缺失",
    "质保期缩短",
    "质量标准",
    "增值服务条款缺失",
    "关键商务条款缺失",
    "键关商务条款缺失",
    "违约金条款弱化",
    "工作日自然日计算口径",
)
CLAUSE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("unit_price", ("价格偏差", "单价", "小计计算", "小计")),
    ("quantity", ("数量差异", "数量", "减少")),
    ("value_added_service", ("增值服务", "值增服务", "免费技术培训", "技术培训", "应急响应")),
    ("payment_terms", ("付款方式", "付款", "预付")),
    ("tax_rate", ("税率", "增值税")),
    ("warranty_period", ("质保期", "质量保证期", "保证期")),
    ("delivery_period", ("交货", "交付时间", "交付周期", "工作日自然日", "计算口径")),
    ("contract_term", ("合同有效期", "服务期限", "有效期")),
    ("breach_liability", ("违约金", "违约责任")),
    ("quality_standard", ("质量标准", "量质标准", "分辨率", "准确率")),
    ("service_scope", ("服务范围", "关键商务条款", "键关商务条款", "服务内容")),
)
MISSING_MARKERS = ("缺失", "未见", "未承接", "未约定", "未明确", "省略")
NO_ISSUE_MARKERS = ("未标注任何差异项", "0处差异", "差异项数量0")


def build_gold_candidates(reference_pdfs: list[Path], parser: PdfParser) -> list[GoldIssue]:
    """按参考答案中的独立编号条目生成未人工核验候选。"""
    issues: list[GoldIssue] = []
    for path in sorted(reference_pdfs):
        sample_match = SAMPLE_PATTERN.search(path.name)
        if sample_match is None:
            continue
        sample_id = sample_match.group(1)
        validate_gold_sample(sample_id)
        document = parser.parse(path, DocumentRole.REFERENCE_ANSWER)
        for entry_index, entry in enumerate(_reference_entries(document.cleaned_text), start=1):
            clause_code = _entry_clause_code(entry)
            if clause_code is None:
                continue
            issues.append(
                GoldIssue(
                    sample_id=sample_id,
                    issue_type=_entry_issue_type(entry),
                    clause_code=clause_code,
                    evidence=entry[:1200],
                    source_file=path.name,
                    candidate_id=_candidate_id(sample_id, entry_index, clause_code, entry),
                    reference_sha256=document.sha256,
                    parsing_warnings=document.quality.warnings,
                    human_verified=False,
                )
            )
    return _deduplicate(issues)


def save_gold_candidates(issues: list[GoldIssue], output_path: Path) -> Path:
    """保存待人工核验的 Gold JSON。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(TypeAdapter(list[GoldIssue]).dump_json(issues, indent=2).decode(), encoding="utf-8")
    return output_path


def save_gold_review_sheet(issues: list[GoldIssue], output_path: Path) -> Path:
    """生成可由有权限人员逐条确认、修改、删除或补充的 CSV。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
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
                "review_action",
                "reviewer",
                "review_date",
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
                    "待确认（确认/修改/删除/补充）",
                    "",
                    "",
                    "",
                    "false",
                ]
            )
    return output_path


def save_gold_manifest(reference_pdfs: list[Path], issues: list[GoldIssue], output_path: Path) -> Path:
    """保存包含零问题样本在内的参考文件覆盖清单。"""
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.sample_id] = counts.get(issue.sample_id, 0) + 1
    samples: list[dict[str, object]] = []
    for path in sorted(reference_pdfs):
        match = SAMPLE_PATTERN.search(path.name)
        if match is None:
            continue
        sample_id = match.group(1)
        samples.append(
            {
                "sample_id": sample_id,
                "reference_file": path.name,
                "sha256": _sha256(path),
                "candidate_count": counts.get(sample_id, 0),
            }
        )
    payload = {"version": "candidate-v2", "human_verified": False, "samples": samples}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def load_gold(path: Path) -> list[GoldIssue]:
    """加载 Gold 文件但不冒充人工核验。"""
    return TypeAdapter(list[GoldIssue]).validate_json(path.read_text(encoding="utf-8"))


def load_verified_gold(path: Path) -> list[GoldIssue]:
    """加载且强制要求人工核验的 Gold。"""
    issues = load_gold(path)
    unverified = [issue.sample_id for issue in issues if not issue.human_verified]
    if unverified:
        message = "Gold 仍含未人工核验记录，禁止计算正式指标: " + ", ".join(sorted(set(unverified)))
        raise ValueError(message)
    return issues


def validate_gold_file(path: Path, manifest_path: Path | None = None) -> GoldValidationReport:
    """检查条款代码、重复项、参考样本覆盖和人工核验状态。"""
    issues = load_gold(path)
    unknown = sorted({issue.clause_code for issue in issues if issue.clause_code not in CLAUSE_BY_CODE})
    candidate_ids = [issue.candidate_id for issue in issues if issue.candidate_id]
    duplicates = sorted({value for value in candidate_ids if candidate_ids.count(value) > 1})
    reference_ids, manifest_warnings = _manifest_sample_ids(manifest_path)
    expected = {f"{sample:03d}" for sample in range(1, 21)}
    missing = sorted(expected - set(reference_ids)) if reference_ids else []
    unverified_count = sum(not issue.human_verified for issue in issues)
    warnings = [*manifest_warnings]
    if unverified_count:
        warnings.append(f"仍有 {unverified_count} 条候选未人工核验")
    valid_structure = not unknown and not duplicates and not missing
    return GoldValidationReport(
        valid_structure=valid_structure,
        ready_for_metrics=valid_structure and unverified_count == 0,
        candidate_count=len(issues),
        reference_sample_ids=reference_ids,
        missing_reference_samples=missing,
        unknown_clause_codes=unknown,
        duplicate_candidate_ids=duplicates,
        unverified_count=unverified_count,
        warnings=warnings,
    )


def _reference_entries(text: str) -> list[str]:
    declared_count = _declared_issue_count(text)
    if declared_count == 0 or any(marker in text for marker in NO_ISSUE_MARKERS):
        return []
    body = text.partition("差异项清单")[2]
    if not body:
        return []
    positions: list[int] = []
    cursor = 0
    maximum = declared_count if declared_count is not None else 9
    for number in range(1, maximum + 1):
        pattern = re.compile(rf"(?<!\d){number}(?!\d)")
        selected: int | None = None
        for match in pattern.finditer(body, cursor):
            preview = body[match.end() : match.end() + 32]
            if _looks_like_entry_header(preview):
                selected = match.start()
                cursor = match.end()
                break
        if selected is None:
            break
        positions.append(selected)
    if not positions:
        return [body.strip()] if _looks_like_entry_header(body[:120]) else []
    entries: list[str] = []
    for index, start in enumerate(positions):
        end = positions[index + 1] if index + 1 < len(positions) else len(body)
        entry = body[start:end].strip("\n ：")
        if entry:
            entries.append(entry)
    if declared_count is not None and (
        len(entries) != declared_count or any(_entry_clause_code(entry) is None for entry in entries)
    ):
        fallback = _entries_from_header_hints(body, declared_count)
        if len(fallback) == declared_count:
            return fallback
    return entries


def _declared_issue_count(text: str) -> int | None:
    header = text.partition("差异项清单")[0]
    tail = header[-160:]
    patterns = (
        re.compile(r"(?<!\d)([0-9])(?=.{0,4}差.{0,10}异)", re.DOTALL),
        re.compile(r"差.{0,10}?([0-9]).{0,10}?异", re.DOTALL),
        re.compile(r"差异.{0,10}?([0-9])", re.DOTALL),
    )
    for pattern in patterns:
        matches = [(match.start(), int(match.group(1))) for match in pattern.finditer(tail)]
        if matches:
            return matches[-1][1]
    return None


def _looks_like_entry_header(preview: str) -> bool:
    return any(token in preview for token in ENTRY_TOKENS)


def _entries_from_header_hints(body: str, declared_count: int) -> list[str]:
    positions = sorted(position for hint in ENTRY_START_HINTS if (position := body.find(hint)) >= 0)
    distinct: list[int] = []
    for position in positions:
        if not distinct or position - distinct[-1] >= 40:
            distinct.append(position)
    if len(distinct) < declared_count:
        return []
    selected = distinct[:declared_count]
    return [
        body[start : selected[index + 1] if index + 1 < len(selected) else len(body)].strip("\n ：")
        for index, start in enumerate(selected)
    ]


def _entry_clause_code(entry: str) -> str | None:
    for code, hints in CLAUSE_HINTS:
        if any(hint in entry[:240] for hint in hints):
            return code
    clause = detect_clause(entry[:500])
    return clause.code if clause is not None else None


def _entry_issue_type(entry: str) -> Verdict:
    return Verdict.MISSING if any(marker in entry for marker in MISSING_MARKERS) else Verdict.DIFFERENT


def _candidate_id(sample_id: str, entry_index: int, clause_code: str, entry: str) -> str:
    raw = f"{sample_id}|{entry_index}|{clause_code}|{_compact(entry)[:300]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _deduplicate(issues: list[GoldIssue]) -> list[GoldIssue]:
    unique: dict[tuple[str, Verdict, str, str], GoldIssue] = {}
    for issue in issues:
        key = (issue.sample_id, issue.issue_type, issue.clause_code, _compact(issue.evidence)[:240])
        unique[key] = issue
    return list(unique.values())


def _manifest_sample_ids(manifest_path: Path | None) -> tuple[list[str], list[str]]:
    if manifest_path is None or not manifest_path.is_file():
        return [], ["未提供 Gold manifest，无法确认 001-020 参考文件完整覆盖"]
    payload = cast(dict[str, Any], json.loads(manifest_path.read_text(encoding="utf-8")))
    samples = cast(list[dict[str, Any]], payload.get("samples", []))
    return sorted(str(sample.get("sample_id", "")) for sample in samples if sample.get("sample_id")), []


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()
