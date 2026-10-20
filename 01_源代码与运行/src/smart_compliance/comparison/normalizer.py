"""合同条款的确定性归一化工具。"""

import re
from decimal import Decimal, InvalidOperation

MONEY_PATTERN = re.compile(r"([¥￥])?\s*([0-9][0-9,]*(?:\.\d+)?)\s*(万|元)?")
PERCENT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*[%％‰]")
TIME_PATTERN = re.compile(r"(\d+)\s*(个?工作日|个?自然日|日|天|小时|分钟|个月|月|年)")
DATE_PATTERN = re.compile(r"(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*日?")
QUANTITY_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(台|套|件|个|批|项|份|本|箱|人次|次)")
CURRENCY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("CNY", re.compile(r"[¥￥]|人民币|CNY|RMB", re.IGNORECASE)),
    ("USD", re.compile(r"\$|美元|USD", re.IGNORECASE)),
    ("EUR", re.compile(r"€|欧元|EUR", re.IGNORECASE)),
)
NEGATION_PATTERN = re.compile(r"不得|不予|无需|不含|不包括|禁止|免除|取消|不承担|无须")
EXCEPTION_PATTERN = re.compile(r"除.{0,30}外|但书|例外|除非|不适用")


def compact_text(text: str) -> str:
    """删除不影响中文语义比对的空白与常见标点。"""
    return re.sub(r"[\s，。；：、（）()《》【】\[\]“”'\"]+", "", text).lower()


def money_values(text: str) -> set[Decimal]:
    """提取金额并统一为元。

    Args:
        text: 条款文本。

    Returns:
        金额集合。
    """
    values: set[Decimal] = set()
    for symbol, raw_value, unit in MONEY_PATTERN.findall(text):
        if not symbol and unit not in {"万", "元"}:
            continue
        try:
            value = Decimal(raw_value.replace(",", ""))
        except InvalidOperation:
            continue
        values.add(value * 10000 if unit == "万" else value)
    return values


def percentage_values(text: str) -> set[Decimal]:
    """提取百分比/千分比数值。"""
    values: set[Decimal] = set()
    for match in PERCENT_PATTERN.finditer(text):
        value = Decimal(match.group(1))
        if "‰" in match.group(0):
            value /= Decimal(10)
        values.add(value)
    return values


def time_values(text: str) -> set[str]:
    """提取带单位的时长约束。"""
    return {f"{value}{unit.replace('个', '')}" for value, unit in TIME_PATTERN.findall(text)}


def date_values(text: str) -> set[str]:
    """提取并统一日期为 YYYY-MM-DD。"""
    return {f"{int(year):04d}-{int(month):02d}-{int(day):02d}" for year, month, day in DATE_PATTERN.findall(text)}


def quantity_values(text: str) -> set[str]:
    """提取带单位的数量，避免把编号误当作数量。"""
    return {f"{value}{unit}" for value, unit in QUANTITY_PATTERN.findall(text)}


def currency_values(text: str) -> set[str]:
    """提取显式币种；没有显式币种时返回空集合。"""
    return {code for code, pattern in CURRENCY_PATTERNS if pattern.search(text)}


def negation_markers(text: str) -> set[str]:
    """提取影响责任或范围判断的否定标记。"""
    return set(NEGATION_PATTERN.findall(text))


def exception_markers(text: str) -> set[str]:
    """提取可能缩小条款适用范围的例外条件。"""
    return set(EXCEPTION_PATTERN.findall(text))


def organization_names(text: str) -> set[str]:
    """提取常见公司名称。"""
    return set(re.findall(r"[\u4e00-\u9fffA-Za-z0-9（）()]{4,40}(?:有限责任公司|股份有限公司|有限公司)", text))


def key_constraints(text: str) -> set[str]:
    """提取金额、百分比、日期和时长等确定性约束。"""
    constraints = {f"money:{value}" for value in money_values(text)}
    constraints.update(f"percent:{value}" for value in percentage_values(text))
    constraints.update(f"time:{value}" for value in time_values(text))
    constraints.update(f"date:{value}" for value in date_values(text))
    constraints.update(f"quantity:{value}" for value in quantity_values(text))
    return constraints
