"""LLM 输出结构校验。"""

import json

import pytest

from smart_compliance.comparison.validator import validate_model_output, validate_model_payload
from smart_compliance.domain.exceptions import ModelOutputError
from smart_compliance.domain.models import DocumentRole, ParsedDocument, ReviewReport
from tests.conftest import parsed_document


def test_valid_json_report(sample_report: ReviewReport) -> None:
    """合法 JSON 报告应通过。"""
    validated = validate_model_output(sample_report.model_dump_json())
    assert validated.report_id == sample_report.report_id


def test_non_json_output_is_rejected() -> None:
    """额外自然语言不得被拼凑成成功报告。"""
    with pytest.raises(ModelOutputError, match="不是单个 JSON 对象"):
        validate_model_output("结论如下：没有问题")


def _payload(**item_updates: object) -> str:
    item: dict[str, object] = {
        "clause_code": "payment_terms",
        "verdict": "different",
        "procurement_evidence": {"quote": "验收合格后15日内付款", "page": 1, "chunk_id": "result-p1-c1"},
        "contract_evidence": {"quote": "验收合格后30日内付款", "page": 1, "chunk_id": "contract-p1-c1"},
        "difference": "付款账期由15日改为30日。",
        "confidence": 0.95,
        "needs_human_review": True,
    }
    item.update(item_updates)
    return json.dumps(
        {"covered_clause_codes": ["acceptance", "payment_terms"], "items": [item], "limitations": []},
        ensure_ascii=False,
    )


def _documents() -> tuple[ParsedDocument, ParsedDocument]:
    procurement = parsed_document(
        "付款方式：验收合格后15日内付款", DocumentRole.PROCUREMENT_RESULT, "FSC-2026-005_采购结果.pdf"
    )
    contract = parsed_document("付款方式：验收合格后30日内付款", DocumentRole.CONTRACT, "FSC-2026-005_采购合同.pdf")
    return procurement, contract


def test_payload_evidence_is_verified_against_chunk() -> None:
    """双方原文、页码和 chunk 均正确时通过证据回查。"""
    procurement, contract = _documents()
    validation = validate_model_payload(_payload(), procurement, contract)
    assert validation.payload.items[0].clause_code == "payment_terms"
    assert validation.warnings == []


@pytest.mark.parametrize(
    ("update", "message"),
    [
        (
            {"procurement_evidence": {"quote": "伪造的付款原文", "page": 1, "chunk_id": "result-p1-c1"}},
            "quote 无法",
        ),
        (
            {"procurement_evidence": {"quote": "验收合格后15日内付款", "page": 2, "chunk_id": "result-p1-c1"}},
            "页码",
        ),
        (
            {"procurement_evidence": {"quote": "验收合格后15日内付款", "page": 1, "chunk_id": "missing"}},
            "chunk_id",
        ),
    ],
)
def test_forged_or_mislocated_evidence_is_rejected(update: dict[str, object], message: str) -> None:
    """伪造 quote、错误页码和错误 chunk 必须失败。"""
    procurement, contract = _documents()
    with pytest.raises(ModelOutputError, match=message):
        validate_model_payload(_payload(**update), procurement, contract)


def test_model_cannot_forge_runtime_metadata() -> None:
    """模型载荷夹带 provider 等运行元数据时 Schema 必须拒绝。"""
    procurement, contract = _documents()
    raw = json.loads(_payload())
    raw["provider"] = "forged"
    with pytest.raises(ModelOutputError, match="Schema"):
        validate_model_payload(json.dumps(raw, ensure_ascii=False), procurement, contract)


def test_consistent_item_is_rejected() -> None:
    """v3 载荷只能输出问题，consistent 只能由覆盖编码表示。"""
    procurement, contract = _documents()
    with pytest.raises(ModelOutputError, match="Schema"):
        validate_model_payload(_payload(verdict="consistent"), procurement, contract)


def test_unknown_or_missing_covered_code_is_rejected() -> None:
    """覆盖清单不得伪造编码，且每个问题编码必须列入清单。"""
    procurement, contract = _documents()
    raw = json.loads(_payload())
    raw["covered_clause_codes"] = ["contract_amount"]
    with pytest.raises(ModelOutputError, match=r"未知编码|未列入"):
        validate_model_payload(json.dumps(raw, ensure_ascii=False), procurement, contract)


def test_duplicate_clause_codes_must_be_merged() -> None:
    """同一规范化条款的多个子要求不得拆成重复问题。"""
    procurement, contract = _documents()
    raw = json.loads(_payload())
    raw["items"].append(dict(raw["items"][0]))
    with pytest.raises(ModelOutputError, match="重复 clause_code"):
        validate_model_payload(json.dumps(raw, ensure_ascii=False), procurement, contract)


def test_generic_contract_reference_cannot_support_warranty_issue() -> None:
    """没有具体期限的“按合同约定”不能作为质保期问题依据。"""
    procurement = parsed_document(
        "质量保证期按合同约定执行", DocumentRole.PROCUREMENT_RESULT, "FSC-2026-006_采购结果.pdf"
    )
    contract = parsed_document("质量服务按双方约定执行", DocumentRole.CONTRACT, "FSC-2026-006_采购合同.pdf")
    raw = json.loads(_payload())
    raw["covered_clause_codes"] = ["warranty_period"]
    raw["items"][0].update(
        {
            "clause_code": "warranty_period",
            "verdict": "missing",
            "procurement_evidence": {
                "quote": "质量保证期按合同约定执行",
                "page": 1,
                "chunk_id": "result-p1-c1",
            },
            "contract_evidence": {"quote": "", "page": None, "chunk_id": None},
        }
    )
    with pytest.raises(ModelOutputError, match="不能支持 warranty_period"):
        validate_model_payload(json.dumps(raw, ensure_ascii=False), procurement, contract)
