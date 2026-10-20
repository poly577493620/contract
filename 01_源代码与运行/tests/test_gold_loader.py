"""Gold 候选拆分与人工核验边界。"""

from pathlib import Path
from typing import cast

from smart_compliance.domain.models import DocumentRole, GoldIssue, ParsedDocument, Verdict
from smart_compliance.evaluation.gold_loader import build_gold_candidates
from smart_compliance.parsing.pdf_parser import PdfParser
from tests.conftest import parsed_document


class FakeReferenceParser:
    """返回预构造参考答案文本。"""

    def __init__(self, document: ParsedDocument) -> None:
        self.document = document

    def parse(self, path: Path, role: DocumentRole) -> ParsedDocument:
        del path, role
        return self.document


def _issues(text: str, sample_id: str = "006") -> list[GoldIssue]:
    document = parsed_document(text, DocumentRole.REFERENCE_ANSWER, f"FSC-2026-{sample_id}_参考答案.pdf")
    parser = cast(PdfParser, FakeReferenceParser(document))
    return build_gold_candidates([Path(f"FSC-2026-{sample_id}_参考答案.pdf")], parser)


def test_mixed_missing_and_different_entries_are_classified_individually() -> None:
    """同一参考答案中的差异和缺失不能被统一判成同一类型。"""
    issues = _issues(
        "本案例共2处差异\n差异项清单\n"
        "1 付款方式差异：采购结果验收后15日付款，合同改为30日付款。\n"
        "2 增值服务条款缺失：免费技术培训和应急响应未承接。"
    )
    assert [(issue.clause_code, issue.issue_type) for issue in issues] == [
        ("payment_terms", Verdict.DIFFERENT),
        ("value_added_service", Verdict.MISSING),
    ]
    assert all(not issue.human_verified for issue in issues)


def test_no_issue_reference_produces_no_candidates() -> None:
    """零问题样本仍应进入 manifest，但不应生成虚假问题。"""
    assert _issues("本案例未标注任何差异项。\n差异项清单", "001") == []


def test_public_006_style_value_added_service_is_detected() -> None:
    """乱码阅读顺序附近的公开 006 增值服务缺失仍可识别。"""
    issues = _issues(
        "本案例共1处差异\n差异项清单\n1 值增服务条款缺失：合同未见免费技术培训，每季度1次；应急响应未承接。"
    )
    assert len(issues) == 1
    assert issues[0].clause_code == "value_added_service"
    assert issues[0].issue_type == Verdict.MISSING
