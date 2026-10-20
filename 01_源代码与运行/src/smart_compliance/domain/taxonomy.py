"""采购合同条款分类与别名。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClauseDefinition:
    """规范化条款定义。"""

    code: str
    name: str
    dimension: str
    keywords: tuple[str, ...]
    risk_level: str


CLAUSES: tuple[ClauseDefinition, ...] = (
    ClauseDefinition("supplier", "供应商/签约主体", "business", ("供应商", "中选人", "中标人", "乙方"), "high"),
    ClauseDefinition("project_name", "项目名称", "business", ("项目名称", "采购项目", "合同名称"), "low"),
    ClauseDefinition(
        "total_price",
        "项目总价",
        "business",
        ("项目总价", "合同总价", "合同总额", "合同金额", "中标金额", "成交金额"),
        "high",
    ),
    ClauseDefinition("unit_price", "单价/明细价格", "business", ("单价", "价格偏差", "明细价格", "小计"), "high"),
    ClauseDefinition("quantity", "数量/规模", "business", ("数量", "采购数量", "服务数量", "规模"), "medium"),
    ClauseDefinition("service_scope", "服务范围", "business", ("服务范围", "服务内容", "采购内容", "工作内容"), "high"),
    ClauseDefinition(
        "value_added_service", "增值服务", "business", ("增值服务", "免费培训", "技术培训", "应急响应"), "medium"
    ),
    ClauseDefinition("delivery_period", "交付周期", "time", ("交付周期", "交货期", "送达", "交付时间"), "high"),
    ClauseDefinition("contract_term", "合同/服务期限", "time", ("合同期限", "服务期限", "有效期", "履行期限"), "high"),
    ClauseDefinition("acceptance", "验收条件", "business", ("验收", "验收标准", "验收合格"), "medium"),
    ClauseDefinition("payment_terms", "付款条件", "business", ("付款", "支付", "结清", "账期"), "high"),
    ClauseDefinition("tax_rate", "税率说明", "business", ("税率", "增值税", "含税", "不含税"), "medium"),
    ClauseDefinition("warranty_period", "质量保证期", "time", ("质保期", "质量保证期", "保证期"), "high"),
    ClauseDefinition(
        "quality_standard",
        "质量/技术标准",
        "business",
        ("质量标准", "技术标准", "分辨率", "准确率", "服务标准"),
        "high",
    ),
    ClauseDefinition("breach_liability", "违约责任", "legal", ("违约责任", "违约金", "赔偿责任"), "high"),
    ClauseDefinition("confidentiality", "保密条款", "legal", ("保密", "商业秘密", "保密义务"), "high"),
)

CLAUSE_BY_CODE = {clause.code: clause for clause in CLAUSES}


def detect_clause(text: str) -> ClauseDefinition | None:
    """根据关键词识别最可能的规范条款。

    Args:
        text: 待识别文本。

    Returns:
        匹配的条款定义；无匹配时返回 None。
    """
    matches = [clause for clause in CLAUSES if any(keyword in text for keyword in clause.keywords)]
    if not matches:
        return None
    return max(matches, key=lambda clause: max(len(keyword) for keyword in clause.keywords if keyword in text))
