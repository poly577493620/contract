"""模型原始输出的严格解析与验证。"""

import re
import unicodedata
from dataclasses import dataclass
from typing import NamedTuple

from pydantic import ValidationError

from smart_compliance.domain.exceptions import ModelOutputError
from smart_compliance.domain.models import (
    Evidence,
    ModelReviewItem,
    ModelReviewPayload,
    ParsedDocument,
    ReviewReport,
    TextChunk,
    Verdict,
)
from smart_compliance.domain.taxonomy import CLAUSE_BY_CODE, CLAUSES

CODE_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


class ModelPayloadValidation(NamedTuple):
    """通过 Schema 与证据回查的模型载荷。"""

    payload: ModelReviewPayload
    warnings: list[str]


@dataclass(slots=True)
class _PayloadValidationContext:
    procurement: ParsedDocument
    contract: ParsedDocument
    covered_codes: set[str]
    errors: list[str]
    seen_codes: set[str]
    seen_items: set[tuple[str, str, str]]


def validate_model_output(raw_output: str) -> ReviewReport:
    """把模型输出验证为质检报告。

    Args:
        raw_output: 模型响应正文。

    Returns:
        结构合法的报告。

    Raises:
        ModelOutputError: 不是单个合法 JSON 对象或 schema 不匹配。
    """
    content = _json_content(raw_output)
    try:
        return ReviewReport.model_validate_json(content)
    except ValidationError as error:
        message = f"模型输出未通过 JSON Schema 校验: {error}"
        raise ModelOutputError(message) from error


def validate_model_payload(
    raw_output: str,
    procurement: ParsedDocument,
    contract: ParsedDocument,
) -> ModelPayloadValidation:
    """校验精简载荷，并将每条证据回查至指定文档分段。"""
    content = _json_content(raw_output)
    try:
        payload = ModelReviewPayload.model_validate_json(content)
    except ValidationError as error:
        message = f"模型输出未通过精简 JSON Schema 校验: {error}"
        raise ModelOutputError(message) from error
    errors: list[str] = []
    covered_codes = set(payload.covered_clause_codes)
    if len(covered_codes) != len(payload.covered_clause_codes):
        errors.append("covered_clause_codes 包含重复编码")
    unknown_covered_codes = sorted(covered_codes - CLAUSE_BY_CODE.keys())
    if unknown_covered_codes:
        errors.append(f"covered_clause_codes 包含未知编码: {', '.join(unknown_covered_codes)}")
    context = _PayloadValidationContext(procurement, contract, covered_codes, errors, set(), set())
    for index, item in enumerate(payload.items, start=1):
        _validate_item(index, item, context)
    if errors:
        raise ModelOutputError("模型证据校验失败: " + "；".join(errors[:8]))
    expected_codes = {
        clause.code for clause in CLAUSES if any(keyword in procurement.cleaned_text for keyword in clause.keywords)
    }
    missing_codes = sorted(expected_codes - covered_codes)
    warnings = [f"模型未覆盖采购结果候选条款: {', '.join(missing_codes)}"] if missing_codes else []
    return ModelPayloadValidation(payload, warnings)


def _validate_item(index: int, item: ModelReviewItem, context: _PayloadValidationContext) -> None:
    if item.clause_code not in CLAUSE_BY_CODE:
        context.errors.append(f"第 {index} 项使用未知 clause_code: {item.clause_code}")
    if item.clause_code not in context.covered_codes:
        context.errors.append(f"第 {index} 项 clause_code 未列入 covered_clause_codes: {item.clause_code}")
    if item.clause_code in context.seen_codes:
        context.errors.append(f"第 {index} 项重复 clause_code: {item.clause_code}；同类子要求必须合并")
    context.seen_codes.add(item.clause_code)
    if item.clause_code == "service_scope" and _is_value_added_service(item.procurement_evidence.quote):
        context.errors.append(f"第 {index} 项培训/应急响应必须归一化为 value_added_service")
    if item.clause_code == "warranty_period" and _is_non_substantive_reference(item.procurement_evidence.quote):
        context.errors.append(f"第 {index} 项仅有泛化合同引用，不能支持 warranty_period 问题")
    _validate_evidence(item.procurement_evidence, context.procurement, f"第 {index} 项采购结果", context.errors)
    if item.verdict == Verdict.MISSING:
        if item.contract_evidence.quote or item.contract_evidence.page or item.contract_evidence.chunk_id:
            context.errors.append(f"第 {index} 项为 missing，但合同证据不为空")
    else:
        _validate_evidence(item.contract_evidence, context.contract, f"第 {index} 项合同", context.errors)
    duplicate_key = (
        item.clause_code,
        _normalize_quote(item.procurement_evidence.quote),
        _normalize_quote(item.contract_evidence.quote),
    )
    if duplicate_key in context.seen_items:
        context.errors.append(f"第 {index} 项与前项重复: {item.clause_code}")
    context.seen_items.add(duplicate_key)


def _json_content(raw_output: str) -> str:
    content = raw_output.strip()
    match = CODE_FENCE.fullmatch(content)
    if match:
        content = match.group(1).strip()
    if not content.startswith("{") or not content.endswith("}"):
        raise ModelOutputError("模型输出不是单个 JSON 对象。")
    return content


def _validate_evidence(
    evidence: Evidence,
    document: ParsedDocument,
    label: str,
    errors: list[str],
) -> None:
    if not evidence.quote:
        errors.append(f"{label}证据为空")
        return
    if evidence.chunk_id is None or evidence.page is None:
        errors.append(f"{label}证据缺少页码或 chunk_id")
        return
    chunks = {chunk.chunk_id: chunk for chunk in document.chunks}
    chunk = chunks.get(evidence.chunk_id)
    if chunk is None:
        errors.append(f"{label}引用不存在的 chunk_id: {evidence.chunk_id}")
        return
    _validate_chunk_location(evidence, chunk, label, errors)


def _validate_chunk_location(evidence: Evidence, chunk: TextChunk, label: str, errors: list[str]) -> None:
    if evidence.page is None:
        return
    if not chunk.page_start <= evidence.page <= chunk.page_end:
        errors.append(f"{label}页码与 chunk 范围不一致")
    quote = _normalize_quote(evidence.quote)
    if quote not in _normalize_quote(chunk.text):
        errors.append(f"{label}quote 无法在指定 chunk 原文中回查")


def _normalize_quote(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", "", normalized)


def _is_value_added_service(text: str) -> bool:
    return any(keyword in text for keyword in ("培训", "应急响应", "7×24", "7x24"))


def _is_non_substantive_reference(text: str) -> bool:
    compact = _normalize_quote(text)
    has_reference = any(marker in compact for marker in ("按合同约定", "以合同约定", "详见合同"))
    has_concrete_period = bool(re.search(r"\d+(?:个?工作日|个?自然日|日|天|个月|月|年)", compact))
    return has_reference and not has_concrete_period
