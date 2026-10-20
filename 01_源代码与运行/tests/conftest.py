"""测试公共夹具。"""

from pathlib import Path

import pytest

from smart_compliance.domain.models import (
    Dimension,
    DocumentRole,
    Evidence,
    ModelInfo,
    ParsedDocument,
    ParsingQuality,
    ReviewItem,
    ReviewReport,
    ReviewSummary,
    RiskLevel,
    TextChunk,
    Verdict,
)


def quality() -> ParsingQuality:
    """创建无警告解析质量。"""
    return ParsingQuality(
        page_count=1,
        raw_characters=100,
        cleaned_characters=100,
        removed_watermark_candidates=0,
        retained_watermark_candidates=0,
        removed_header_footer_lines=0,
        abnormal_character_ratio=0,
        blank_pages=[],
        low_quality_pages=[],
        needs_human_review=False,
    )


def parsed_document(text: str, role: DocumentRole, source_name: str) -> ParsedDocument:
    """创建单分段解析文档。"""
    prefix = "result" if role == DocumentRole.PROCUREMENT_RESULT else "contract"
    return ParsedDocument(
        role=role,
        source_name=source_name,
        sha256="a" * 64,
        raw_text=text,
        cleaned_text=text,
        chunks=[TextChunk(chunk_id=f"{prefix}-p1-c1", page_start=1, page_end=1, text=text)],
        quality=quality(),
        ocr_source="page_image",
        ocr_text_sha256="b" * 64,
    )


def pytest_addoption(parser: pytest.Parser) -> None:
    """增加显式真实 OCR 集成测试开关。"""
    parser.addoption("--run-integration", action="store_true", default=False)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """默认跳过耗时或外部服务集成测试。"""
    if config.getoption("--run-integration"):
        return
    marker = pytest.mark.skip(reason="需要显式 --run-integration")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(marker)


@pytest.fixture
def sample_report() -> ReviewReport:
    """创建可供导出测试的报告。"""
    item = ReviewItem(
        item_id="test-payment",
        source_order=1,
        clause_code="payment_terms",
        clause_name="付款条件",
        dimension=Dimension.BUSINESS,
        verdict=Verdict.DIFFERENT,
        procurement_evidence=Evidence(quote="验收合格后15日内付款", page=1, chunk_id="result-p1-c1"),
        contract_evidence=Evidence(quote="验收合格后30日内付款", page=2, chunk_id="contract-p2-c1"),
        difference="账期从15日变为30日。",
        risk_level=RiskLevel.HIGH,
        risk_reason="付款账期偏离采购结果。",
        recommendation="请采购和法务复核。",
        confidence=0.95,
        needs_human_review=True,
    )
    return ReviewReport(
        sample_id="005",
        project_name="2026年度全行办公用品采购项目",
        model=ModelInfo(provider="local", model_name="rules", prompt_version="test"),
        summary=ReviewSummary(
            overall_risk=RiskLevel.HIGH,
            consistent_count=0,
            missing_count=0,
            different_count=1,
            needs_human_review=True,
        ),
        items=[item],
        parsing_quality={"procurement_result": quality(), "contract": quality()},
        source_hashes={
            "procurement_result_pdf": "a" * 64,
            "contract_pdf": "b" * 64,
            "procurement_result_ocr_text": "c" * 64,
            "contract_ocr_text": "d" * 64,
        },
        limitations=["测试局限。"],
    )


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    """返回报告输出目录。"""
    return tmp_path / "reports"
