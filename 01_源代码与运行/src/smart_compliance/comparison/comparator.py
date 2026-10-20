"""规则基线与 LLM 合同比对编排。"""

import json
import re
from pathlib import Path

from smart_compliance.comparison.prompt_builder import PROMPT_VERSION, PromptBuilder
from smart_compliance.comparison.rules import (
    candidate_source_order,
    collect_candidate_groups,
    decide_clause,
    match_candidate,
)
from smart_compliance.comparison.validator import ModelPayloadValidation, validate_model_payload
from smart_compliance.config import Settings, portable_project_path
from smart_compliance.domain.exceptions import ModelInvocationError, ModelOutputError
from smart_compliance.domain.models import (
    Dimension,
    Evidence,
    ModelInfo,
    ParsedDocument,
    ReviewItem,
    ReviewReport,
    ReviewSummary,
    RiskLevel,
    ValidationStatus,
    Verdict,
)
from smart_compliance.domain.taxonomy import CLAUSE_BY_CODE
from smart_compliance.llm.base import LlmProvider, LlmResponse


class RuleBasedComparator:
    """可离线运行、证据优先的确定性基线。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化规则比对器。"""
        self._settings = settings or Settings()

    def compare(self, procurement: ParsedDocument, contract: ParsedDocument) -> ReviewReport:
        """生成规则基线质检报告。

        Args:
            procurement: 采购结果解析结果。
            contract: 合同解析结果。

        Returns:
            三分类质检报告。
        """
        source_candidates = collect_candidate_groups(procurement)
        contract_candidates = collect_candidate_groups(contract)
        ordered_candidates = candidate_source_order(procurement, source_candidates)
        available_contract = {code: list(candidates) for code, candidates in contract_candidates.items()}
        items: list[ReviewItem] = []
        for order, source in enumerate(ordered_candidates, start=1):
            code = source.clause.code
            contract_candidate = match_candidate(source, available_contract.get(code, []))
            if contract_candidate is not None:
                available_contract[code].remove(contract_candidate)
            decision = decide_clause(source, contract_candidate)
            needs_review = decision.confidence < self._settings.low_confidence_threshold
            needs_review = needs_review or procurement.quality.needs_human_review or contract.quality.needs_human_review
            items.append(
                ReviewItem(
                    item_id=f"rule-{order:03d}-{code}",
                    source_order=order,
                    clause_code=code,
                    clause_name=source.clause.name,
                    dimension=Dimension(source.clause.dimension),
                    verdict=decision.verdict,
                    procurement_evidence=source.evidence,
                    contract_evidence=contract_candidate.evidence if contract_candidate else Evidence(quote=""),
                    difference=decision.difference,
                    risk_level=(
                        RiskLevel.LOW if decision.verdict == Verdict.CONSISTENT else RiskLevel(source.clause.risk_level)
                    ),
                    risk_reason=_risk_reason(decision.verdict, source.clause.name),
                    recommendation=_recommendation(decision.verdict),
                    confidence=decision.confidence,
                    needs_human_review=needs_review,
                )
            )
        return _build_report(procurement, contract, items)


class LlmComparator:
    """调用统一 provider 并严格验证结构化输出。"""

    def __init__(
        self,
        provider: LlmProvider,
        prompt_builder: PromptBuilder | None = None,
        settings: Settings | None = None,
    ) -> None:
        """初始化 LLM 比对器。

        Args:
            provider: 模型 provider。
            prompt_builder: 可替换提示词构建器。
        """
        self._provider = provider
        self._prompt_builder = prompt_builder or PromptBuilder()
        self._settings = settings or Settings()

    async def compare(
        self, procurement: ParsedDocument, contract: ParsedDocument, raw_output_path: Path
    ) -> ReviewReport:
        """调用模型并保存原始响应。

        Args:
            procurement: 采购结果解析结果。
            contract: 合同解析结果。
            raw_output_path: 项目内原始响应保存路径。

        Returns:
            结构合法的模型报告。
        """
        messages = self._prompt_builder.build_messages(procurement, contract)
        try:
            response = await self._provider.complete(messages)
        except ModelInvocationError as error:
            _write_invocation_record(raw_output_path, self._provider, error)
            raise
        raw_output_path.parent.mkdir(parents=True, exist_ok=True)
        raw_output_path.write_text(response.content, encoding="utf-8")
        _write_success_invocation_record(raw_output_path, response)
        try:
            validation = validate_model_payload(response.content, procurement, contract)
        except ModelOutputError as error:
            _write_validation_record(raw_output_path, ValidationStatus.FAILED, [], str(error))
            error.retry_count = response.retry_count
            error.elapsed_ms = response.latency_ms
            raise
        _write_validation_record(raw_output_path, _validation_status(validation), validation.warnings, None)
        return _build_llm_report((procurement, contract), validation, response, raw_output_path, self._settings)


def _build_report(
    procurement: ParsedDocument,
    contract: ParsedDocument,
    items: list[ReviewItem],
) -> ReviewReport:
    counts = {verdict: sum(item.verdict == verdict for item in items) for verdict in Verdict}
    issue_risks = [item.risk_level for item in items if item.verdict != Verdict.CONSISTENT]
    overall_risk = max(issue_risks, key=_risk_rank, default=RiskLevel.LOW)
    sample_id_match = re.search(r"(?:^|[-_])(\d{3})(?:[-_]|\.)", procurement.source_name)
    project_name = _project_name(procurement)
    return ReviewReport(
        sample_id=sample_id_match.group(1) if sample_id_match else None,
        project_name=project_name,
        model=ModelInfo(provider="local", model_name="deterministic-rule-baseline", prompt_version=PROMPT_VERSION),
        summary=ReviewSummary(
            overall_risk=overall_risk,
            consistent_count=counts[Verdict.CONSISTENT],
            missing_count=counts[Verdict.MISSING],
            different_count=counts[Verdict.DIFFERENT],
            needs_human_review=any(item.needs_human_review for item in items),
        ),
        items=items,
        parsing_quality={"procurement_result": procurement.quality, "contract": contract.quality},
        source_hashes=_source_hashes(procurement, contract),
        limitations=[
            "当前报告由离线确定性基线生成，复杂语义、隐含条件与法律效果仍需人工或已配置 LLM 复核。",
            "低质量 PDF 页面可能影响证据定位和条款覆盖。",
        ],
    )


def _build_llm_report(
    documents: tuple[ParsedDocument, ParsedDocument],
    validation: ModelPayloadValidation,
    response: LlmResponse,
    raw_output_path: Path,
    settings: Settings,
) -> ReviewReport:
    procurement, contract = documents
    ordered = sorted(
        validation.payload.items, key=lambda item: _evidence_position(procurement, item.procurement_evidence)
    )
    items: list[ReviewItem] = []
    parsing_needs_review = procurement.quality.needs_human_review or contract.quality.needs_human_review
    for order, model_item in enumerate(ordered, start=1):
        clause = CLAUSE_BY_CODE[model_item.clause_code]
        needs_review = model_item.needs_human_review or model_item.confidence < settings.low_confidence_threshold
        needs_review = needs_review or parsing_needs_review or bool(validation.warnings)
        items.append(
            ReviewItem(
                item_id=f"llm-{order:03d}-{model_item.clause_code}",
                source_order=order,
                clause_code=model_item.clause_code,
                clause_name=clause.name,
                dimension=Dimension(clause.dimension),
                verdict=model_item.verdict,
                procurement_evidence=model_item.procurement_evidence,
                contract_evidence=model_item.contract_evidence,
                difference=model_item.difference,
                risk_level=RiskLevel(clause.risk_level),
                risk_reason=_risk_reason(model_item.verdict, clause.name),
                recommendation=_recommendation(model_item.verdict),
                confidence=model_item.confidence,
                needs_human_review=needs_review,
            )
        )
    counts = {verdict: sum(item.verdict == verdict for item in items) for verdict in Verdict}
    issue_risks = [item.risk_level for item in items if item.verdict != Verdict.CONSISTENT]
    sample_match = re.search(r"(?:^|[-_])(\d{3})(?:[-_]|\.)", procurement.source_name)
    return ReviewReport(
        sample_id=sample_match.group(1) if sample_match else None,
        project_name=_project_name(procurement),
        model=ModelInfo(
            provider=response.provider_name,
            model_name=response.model_name,
            prompt_version=PROMPT_VERSION,
            invoked_at=response.invoked_at,
            api_path=response.api_path,
            latency_ms=response.latency_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            estimated_cost=response.estimated_cost,
            retry_count=response.retry_count,
            finish_reason=response.finish_reason,
            raw_output_file=portable_project_path(raw_output_path, root=settings.root),
        ),
        summary=ReviewSummary(
            overall_risk=max(issue_risks, key=_risk_rank, default=RiskLevel.LOW),
            consistent_count=counts[Verdict.CONSISTENT],
            missing_count=counts[Verdict.MISSING],
            different_count=counts[Verdict.DIFFERENT],
            covered_clause_count=len(validation.payload.covered_clause_codes),
            needs_human_review=bool(validation.warnings) or any(item.needs_human_review for item in items),
        ),
        items=items,
        parsing_quality={"procurement_result": procurement.quality, "contract": contract.quality},
        source_hashes=_source_hashes(procurement, contract),
        evidence_validation_status=_validation_status(validation),
        validation_warnings=validation.warnings,
        limitations=[
            *validation.payload.limitations,
            "模型结论已经过 Schema 与 chunk 证据回查，但仍须由具备权限的人员复核业务和法律效果。",
        ],
    )


def _evidence_position(document: ParsedDocument, evidence: Evidence) -> tuple[int, int]:
    chunk_order = {chunk.chunk_id: index for index, chunk in enumerate(document.chunks)}
    chunk_index = chunk_order.get(evidence.chunk_id or "", len(document.chunks))
    return chunk_index, evidence.page or 10**9


def _source_hashes(procurement: ParsedDocument, contract: ParsedDocument) -> dict[str, str]:
    hashes = {"procurement_result_pdf": procurement.sha256, "contract_pdf": contract.sha256}
    if procurement.ocr_text_sha256 is not None:
        hashes["procurement_result_ocr_text"] = procurement.ocr_text_sha256
    if contract.ocr_text_sha256 is not None:
        hashes["contract_ocr_text"] = contract.ocr_text_sha256
    return hashes


def _validation_status(validation: ModelPayloadValidation) -> ValidationStatus:
    return ValidationStatus.WARNING if validation.warnings else ValidationStatus.PASSED


def _write_validation_record(
    raw_output_path: Path,
    status: ValidationStatus,
    warnings: list[str],
    error: str | None,
) -> None:
    validation_path = raw_output_path.with_suffix(".validation.json")
    payload = {"status": status.value, "warnings": warnings, "error": error}
    validation_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_invocation_record(
    raw_output_path: Path,
    provider: LlmProvider,
    error: ModelInvocationError,
) -> None:
    raw_output_path.parent.mkdir(parents=True, exist_ok=True)
    record_path = raw_output_path.with_suffix(".invocation.json")
    payload = {
        "success": False,
        "provider": provider.provider_name,
        "model_name": provider.model_name,
        "prompt_version": PROMPT_VERSION,
        "error_category": error.category,
        "status_code": error.status_code,
        "retry_count": error.retry_count,
        "elapsed_ms": error.elapsed_ms,
        "error": str(error),
        "diagnostics": error.diagnostics,
        "attempts": error.attempts,
    }
    record_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_success_invocation_record(raw_output_path: Path, response: LlmResponse) -> None:
    record_path = raw_output_path.with_suffix(".invocation.json")
    payload = {
        "success": True,
        "provider": response.provider_name,
        "model_name": response.model_name,
        "prompt_version": PROMPT_VERSION,
        "finish_reason": response.finish_reason,
        "retry_count": response.retry_count,
        "latency_ms": response.latency_ms,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "response_metadata": response.response_metadata,
        "attempts": [attempt.model_dump(mode="json") for attempt in response.attempts],
    }
    record_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _project_name(document: ParsedDocument) -> str:
    match = re.search(r"关于公布(.{2,80}?项目).{0,12}(?:采购结果|分散采购结果|集中采购结果)", document.cleaned_text)
    if match:
        return match.group(1).strip()
    return Path(document.source_name).stem.replace("_采购结果", "")


def _risk_rank(level: RiskLevel) -> int:
    return {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3}[level]


def _risk_reason(verdict: Verdict, clause_name: str) -> str:
    if verdict == Verdict.CONSISTENT:
        return f"{clause_name}未发现确定性冲突。"
    if verdict == Verdict.MISSING:
        return f"{clause_name}未在合同中得到明确承接，可能影响履约与追责。"
    return f"{clause_name}存在实质差异，可能偏离采购决策。"


def _recommendation(verdict: Verdict) -> str:
    if verdict == Verdict.CONSISTENT:
        return "人工抽样复核证据页码和上下文。"
    return "由采购、业务和法务人员核对原文并确认是否修改合同或补充说明。"
