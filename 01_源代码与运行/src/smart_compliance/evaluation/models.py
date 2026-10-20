"""评测结果模型。"""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from smart_compliance.domain.models import MetricResult


class EvaluationModel(BaseModel):
    """禁止未知字段的评测模型基类。"""

    model_config = ConfigDict(extra="forbid")


class SampleRun(EvaluationModel):
    """一次模型样本运行记录。"""

    model_name: str
    sample_id: str
    success: bool
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    repetition: int = Field(default=1, ge=1)
    provider: str | None = None
    exact_model_name: str | None = None
    prompt_version: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    latency_ms: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)
    retry_count: int = Field(default=0, ge=0)
    finish_reason: str | None = None
    raw_output_file: str | None = None
    error_category: str | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    error: str | None = None
    ocr_source: Literal["page_image"] | None = None
    procurement_ocr_text_sha256: str | None = None
    contract_ocr_text_sha256: str | None = None
    evaluation_mode: Literal["single_call", "two_stage"] = "single_call"


class ModelEvaluation(EvaluationModel):
    """单个模型的聚合结果。"""

    model_name: str
    issue_metrics: MetricResult
    missing_metrics: MetricResult
    different_metrics: MetricResult
    macro_precision: float = Field(default=0, ge=0, le=1)
    macro_recall: float = Field(default=0, ge=0, le=1)
    macro_f1: float = Field(default=0, ge=0, le=1)
    median_latency_ms: float | None = Field(default=None, ge=0)
    p95_latency_ms: float | None = Field(default=None, ge=0)
    total_cost: float | None = Field(default=None, ge=0)
    successful_runs: int = Field(ge=0)
    failed_runs: int = Field(ge=0)
    failure_rate: float = Field(default=0, ge=0, le=1)


class EvaluationReport(EvaluationModel):
    """受控多模型评测报告。"""

    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    prompt_version: str
    sample_ids: list[str]
    repetitions: int = Field(default=1, ge=1)
    input_hashes: dict[str, dict[str, str]] = Field(default_factory=dict)
    ocr_inputs: dict[str, dict[str, object]] = Field(default_factory=dict)
    model_parameters: dict[str, dict[str, str | int | float | bool | None]] = Field(default_factory=dict)
    models: list[ModelEvaluation]
    runs: list[SampleRun]
    caveats: list[str]


class GoldValidationReport(EvaluationModel):
    """Gold 文件结构、覆盖和人工核验状态。"""

    valid_structure: bool
    ready_for_metrics: bool
    candidate_count: int = Field(ge=0)
    reference_sample_ids: list[str]
    missing_reference_samples: list[str]
    unknown_clause_codes: list[str]
    duplicate_candidate_ids: list[str]
    unverified_count: int = Field(ge=0)
    warnings: list[str]


class FieldTypeAccuracy(EvaluationModel):
    """一个字段类型的人工标注命中率。"""

    verified: int = Field(ge=0)
    correct: int = Field(ge=0)
    accuracy: float | None = Field(default=None, ge=0, le=1)


class FieldAccuracyReport(EvaluationModel):
    """解析字段人工标注评测结果。"""

    status: Literal["待标注", "已评测"]
    total_rows: int = Field(ge=0)
    verified_rows: int = Field(ge=0)
    correct_rows: int = Field(ge=0)
    accuracy: float | None = Field(default=None, ge=0, le=1)
    by_field: dict[str, FieldTypeAccuracy]
    warnings: list[str]
