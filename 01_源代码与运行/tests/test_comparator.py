"""公开案例、确定性基线与模型失败审计。"""

import json
from pathlib import Path

import pytest

from smart_compliance.comparison.comparator import LlmComparator, RuleBasedComparator
from smart_compliance.domain.exceptions import ModelInvocationError
from smart_compliance.domain.models import DocumentRole, Verdict
from smart_compliance.llm.base import LlmResponse
from tests.conftest import parsed_document


class FailingProvider:
    """始终返回可分类失败的测试 provider。"""

    name = "failing"
    provider_name = "test-provider"
    model_name = "test-model"

    async def complete(self, messages: list[dict[str, str]]) -> LlmResponse:
        """模拟远端断连。"""
        del messages
        raise ModelInvocationError(
            "模型传输连接被远端中断。",
            category="transport_error",
            retry_count=0,
            elapsed_ms=123,
        )


def test_sample_005_payment_term_difference() -> None:
    """样本005式账期变化应判为差异项。"""
    procurement = parsed_document(
        "项目编号 JZC-2026-005 项目总价 ¥603,000.00 付款方式及规则 验收合格后15日内结清当批货款",
        DocumentRole.PROCUREMENT_RESULT,
        "JZC-2026-005_采购结果.pdf",
    )
    contract = parsed_document(
        "项目编号 JZC-2026-005 合同总价 ¥603,000.00 付款方式 验收合格后30日内结清当批货款",
        DocumentRole.CONTRACT,
        "JZC-2026-005_采购合同.pdf",
    )
    report = RuleBasedComparator().compare(procurement, contract)
    payment = next(item for item in report.items if item.clause_code == "payment_terms")
    assert payment.verdict == Verdict.DIFFERENT
    assert "time:15日" in payment.difference
    assert report.sample_id == "005"


def test_sample_006_value_added_service_missing() -> None:
    """样本006式增值服务未承接应判为缺失项。"""
    procurement = parsed_document(
        "项目编号 FSC-2026-006 免费技术培训每季度1次，每次不少于2小时；提供7×24小时应急响应，2小时内到场。",
        DocumentRole.PROCUREMENT_RESULT,
        "FSC-2026-006_采购结果.pdf",
    )
    contract = parsed_document(
        "项目编号 FSC-2026-006 服务内容为电梯维护保养，按月提交维保记录。",
        DocumentRole.CONTRACT,
        "FSC-2026-006_采购合同.pdf",
    )
    report = RuleBasedComparator().compare(procurement, contract)
    value_added = next(item for item in report.items if item.clause_code == "value_added_service")
    assert value_added.verdict == Verdict.MISSING
    assert value_added.contract_evidence.quote == ""


def test_semantically_equal_delivery_period() -> None:
    """同一工作日时限不应因措辞变化报错。"""
    procurement = parsed_document(
        "交付周期：订单下达后5个工作日内送达。",
        DocumentRole.PROCUREMENT_RESULT,
        "FSC-2026-001_采购结果.pdf",
    )
    contract = parsed_document(
        "交货时间：收到订单之日起5个工作日内完成送达。",
        DocumentRole.CONTRACT,
        "FSC-2026-001_采购合同.pdf",
    )
    report = RuleBasedComparator().compare(procurement, contract)
    delivery = next(item for item in report.items if item.clause_code == "delivery_period")
    assert delivery.verdict == Verdict.CONSISTENT


def test_workday_to_calendar_day_is_a_difference() -> None:
    """工作日和自然日不可视为语义等价。"""
    procurement = parsed_document(
        "交付周期：订单下达后5个工作日内送达。",
        DocumentRole.PROCUREMENT_RESULT,
        "FSC-2026-002_采购结果.pdf",
    )
    contract = parsed_document(
        "交货时间：订单下达后5个自然日内送达。",
        DocumentRole.CONTRACT,
        "FSC-2026-002_采购合同.pdf",
    )
    report = RuleBasedComparator().compare(procurement, contract)
    delivery = next(item for item in report.items if item.clause_code == "delivery_period")
    assert delivery.verdict == Verdict.DIFFERENT
    assert delivery.confidence >= 0.9


def test_negation_or_exception_changes_scope() -> None:
    """否定和例外条件变化必须进入人工关注项。"""
    procurement = parsed_document(
        "服务范围：供应商承担全部系统维护服务。",
        DocumentRole.PROCUREMENT_RESULT,
        "FSC-2026-003_采购结果.pdf",
    )
    contract = parsed_document(
        "服务范围：供应商承担系统维护服务，但硬件故障除外。",
        DocumentRole.CONTRACT,
        "FSC-2026-003_采购合同.pdf",
    )
    report = RuleBasedComparator().compare(procurement, contract)
    scope = next(item for item in report.items if item.clause_code == "service_scope")
    assert scope.verdict == Verdict.DIFFERENT
    assert "例外" in scope.difference


def test_multiple_quantities_are_not_collapsed_or_reused() -> None:
    """同类多商品应形成多个采购候选，且一个合同候选不能重复匹配。"""
    procurement = parsed_document(
        "产品A 数量10台\n产品B 数量20台",
        DocumentRole.PROCUREMENT_RESULT,
        "FSC-2026-004_采购结果.pdf",
    )
    contract = parsed_document(
        "产品A 数量10台",
        DocumentRole.CONTRACT,
        "FSC-2026-004_采购合同.pdf",
    )
    report = RuleBasedComparator().compare(procurement, contract)
    quantities = [item for item in report.items if item.clause_code == "quantity"]
    assert len(quantities) == 2
    assert {item.verdict for item in quantities} == {Verdict.CONSISTENT, Verdict.MISSING}


def test_tax_basis_change_is_a_difference() -> None:
    """相同税率但含税/不含税口径不同仍是差异。"""
    procurement = parsed_document(
        "税率说明：报价为含税价，增值税税率13%。",
        DocumentRole.PROCUREMENT_RESULT,
        "FSC-2026-007_采购结果.pdf",
    )
    contract = parsed_document(
        "税率说明：合同金额为不含税价，增值税税率13%。",
        DocumentRole.CONTRACT,
        "FSC-2026-007_采购合同.pdf",
    )
    report = RuleBasedComparator().compare(procurement, contract)
    tax = next(item for item in report.items if item.clause_code == "tax_rate")
    assert tax.verdict == Verdict.DIFFERENT


@pytest.mark.asyncio
async def test_llm_invocation_failure_writes_audit_record(tmp_path: Path) -> None:
    """模型在返回正文前失败时仍应留下安全审计记录。"""
    procurement = parsed_document("付款条件：15日内付款", DocumentRole.PROCUREMENT_RESULT, "005_采购结果.pdf")
    contract = parsed_document("付款条件：30日内付款", DocumentRole.CONTRACT, "005_采购合同.pdf")
    raw_path = tmp_path / "raw" / "005-failing.txt"
    with pytest.raises(ModelInvocationError, match="远端中断"):
        await LlmComparator(FailingProvider()).compare(procurement, contract, raw_path)
    audit = json.loads(raw_path.with_suffix(".invocation.json").read_text(encoding="utf-8"))
    assert audit["success"] is False
    assert audit["error_category"] == "transport_error"
    assert audit["elapsed_ms"] == 123
