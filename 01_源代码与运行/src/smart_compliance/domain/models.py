"""解析、质检与评测领域模型。"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """禁止未知字段的领域模型基类。"""

    model_config = ConfigDict(extra="forbid")


class DocumentRole(StrEnum):
    """上传文档角色。"""

    PROCUREMENT_RESULT = "procurement_result"
    CONTRACT = "contract"
    REFERENCE_ANSWER = "reference_answer"


class Verdict(StrEnum):
    """合同承接结论。"""

    CONSISTENT = "consistent"
    MISSING = "missing"
    DIFFERENT = "different"


class RiskLevel(StrEnum):
    """风险等级。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Dimension(StrEnum):
    """审查维度。"""

    BUSINESS = "business"
    TIME = "time"
    LEGAL = "legal"
    OTHER = "other"


class ValidationStatus(StrEnum):
    """模型证据校验状态。"""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class TextSpan(StrictModel):
    """PDF 中可回溯的文本片段。"""

    page: int = Field(ge=1)
    text: str
    bbox: tuple[float, float, float, float]
    font_size: float = Field(ge=0)
    color: int | None = None
    direction: tuple[float, float] = (1.0, 0.0)


class OcrTextSpan(StrictModel):
    """页面图片 OCR 中可回溯的文字片段。"""

    page: int = Field(ge=1)
    text: str = Field(min_length=1)
    bbox: tuple[float, float, float, float]
    confidence: float = Field(ge=0, le=1)
    line_id: str
    reading_order: int = Field(ge=1)
    page_image_sha256: str = Field(min_length=64, max_length=64)


class OcrPageQuality(StrictModel):
    """单页 OCR 质量指标。"""

    page: int = Field(ge=1)
    character_count: int = Field(ge=0)
    span_count: int = Field(ge=0)
    mean_confidence: float = Field(ge=0, le=1)
    median_confidence: float = Field(ge=0, le=1)
    low_confidence_ratio: float = Field(ge=0, le=1)
    fragment_line_ratio: float = Field(ge=0, le=1)
    text_box_coverage: float = Field(ge=0, le=1)
    blank: bool
    column_mode: Literal["single", "multi", "table_like"]
    quality_gate_passed: bool


class OcrMetadata(StrictModel):
    """页面渲染和本地 OCR 的可复现元数据。"""

    source: Literal["page_image"] = "page_image"
    pdf_sha256: str = Field(min_length=64, max_length=64)
    text_sha256: str = Field(min_length=64, max_length=64)
    cache_key: str
    cache_dir: str
    renderer: str
    renderer_version: str
    dpi: int = Field(ge=150, le=600)
    attempted_dpis: list[int]
    ocr_engine: str
    ocr_engine_version: str
    ocr_model_fingerprint: str
    page_image_sha256: dict[str, str]
    page_quality: list[OcrPageQuality]
    quality_gate_passed: bool


class RemovedText(StrictModel):
    """被清洗器移除或标记的文本。"""

    page: int = Field(ge=1)
    text: str
    reason: str
    score: float = Field(ge=0, le=1)
    removed: bool


class TextChunk(StrictModel):
    """供检索与模型比对的文本分段。"""

    chunk_id: str
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    text: str = Field(min_length=1)


class ParsingQuality(StrictModel):
    """PDF 解析质量摘要。"""

    page_count: int = Field(ge=0)
    raw_characters: int = Field(ge=0)
    cleaned_characters: int = Field(ge=0)
    removed_watermark_candidates: int = Field(ge=0)
    retained_watermark_candidates: int = Field(ge=0)
    removed_header_footer_lines: int = Field(ge=0)
    abnormal_character_ratio: float = Field(ge=0, le=1)
    blank_pages: list[int]
    low_quality_pages: list[int]
    needs_human_review: bool
    warnings: list[str] = Field(default_factory=list)
    ocr_source: Literal["page_image", "text_layer"] = "text_layer"
    ocr_mean_confidence: float | None = Field(default=None, ge=0, le=1)
    ocr_median_confidence: float | None = Field(default=None, ge=0, le=1)
    ocr_low_confidence_ratio: float | None = Field(default=None, ge=0, le=1)
    ocr_fragment_line_ratio: float | None = Field(default=None, ge=0, le=1)
    ocr_quality_gate_passed: bool | None = None


class ParsedDocument(StrictModel):
    """PDF 解析结果。"""

    role: DocumentRole
    source_name: str
    sha256: str
    parsed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw_text: str
    cleaned_text: str
    chunks: list[TextChunk]
    quality: ParsingQuality
    removed_text: list[RemovedText] = Field(default_factory=list)
    ocr_source: Literal["page_image", "text_layer"] = "text_layer"
    ocr_text_sha256: str | None = None
    ocr_spans: list[OcrTextSpan] = Field(default_factory=list)
    ocr_metadata: OcrMetadata | None = None


class Evidence(StrictModel):
    """文档证据。"""

    quote: str
    page: int | None = Field(default=None, ge=1)
    chunk_id: str | None = None


class ReviewItem(StrictModel):
    """单个条款的质检结论。"""

    item_id: str
    source_order: int = Field(ge=1)
    clause_code: str
    clause_name: str
    dimension: Dimension
    verdict: Verdict
    procurement_evidence: Evidence
    contract_evidence: Evidence
    difference: str
    risk_level: RiskLevel
    risk_reason: str
    recommendation: str
    confidence: float = Field(ge=0, le=1)
    needs_human_review: bool


class ModelInfo(StrictModel):
    """生成报告的模型与运行元数据。"""

    provider: str
    model_name: str
    prompt_version: str
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    invoked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    api_path: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)
    retry_count: int = Field(default=0, ge=0)
    finish_reason: str | None = None
    raw_output_file: str | None = None


class ReviewSummary(StrictModel):
    """质检报告汇总。"""

    overall_risk: RiskLevel
    consistent_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    different_count: int = Field(ge=0)
    covered_clause_count: int | None = Field(default=None, ge=0)
    needs_human_review: bool


class ReviewReport(StrictModel):
    """完整采购合同质检报告。"""

    report_id: str = Field(default_factory=lambda: uuid4().hex)
    sample_id: str | None = None
    project_name: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    model: ModelInfo
    summary: ReviewSummary
    items: list[ReviewItem]
    parsing_quality: dict[str, ParsingQuality]
    source_hashes: dict[str, str] = Field(default_factory=dict)
    evidence_validation_status: ValidationStatus = ValidationStatus.PASSED
    validation_warnings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    disclaimer: str = "教学模拟/测试数据。AI 辅助结果须由具备权限的人员复核，不构成法律或审计结论。"

    @model_validator(mode="after")
    def validate_summary_counts(self) -> Self:
        """验证汇总计数与条目一致。"""
        counts = {verdict: sum(item.verdict == verdict for item in self.items) for verdict in Verdict}
        expected = (
            self.summary.consistent_count,
            self.summary.missing_count,
            self.summary.different_count,
        )
        actual = (counts[Verdict.CONSISTENT], counts[Verdict.MISSING], counts[Verdict.DIFFERENT])
        if expected != actual:
            message = f"汇总计数与明细不一致: expected={expected}, actual={actual}"
            raise ValueError(message)
        return self


class ModelReviewItem(StrictModel):
    """模型只需返回的精简问题条款结论。"""

    clause_code: str = Field(min_length=1)
    verdict: Literal[Verdict.MISSING, Verdict.DIFFERENT]
    procurement_evidence: Evidence
    contract_evidence: Evidence
    difference: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)
    needs_human_review: bool


class ModelReviewPayload(StrictModel):
    """不包含运行时元数据的模型输出载荷。"""

    covered_clause_codes: list[str] = Field(default_factory=list, max_length=30)
    items: list[ModelReviewItem] = Field(max_length=12)
    limitations: list[str] = Field(default_factory=list, max_length=5)


class GoldIssue(StrictModel):
    """训练集参考答案中的规范化问题。"""

    sample_id: str
    issue_type: Verdict
    clause_code: str
    evidence: str
    source_file: str
    candidate_id: str = ""
    reference_sha256: str = ""
    parsing_warnings: list[str] = Field(default_factory=list)
    human_verified: bool = False
    reviewer: str | None = None
    reviewed_at: datetime | None = None
    notes: str | None = None


class PredictionIssue(StrictModel):
    """用于评测匹配的模型预测问题。"""

    sample_id: str
    issue_type: Verdict
    clause_code: str
    item_id: str


class MetricResult(StrictModel):
    """问题识别指标。"""

    true_positive: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    f1: float = Field(ge=0, le=1)
