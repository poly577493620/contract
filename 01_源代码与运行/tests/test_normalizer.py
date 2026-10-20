"""金额、日期与期限归一化。"""

from decimal import Decimal

from smart_compliance.comparison.normalizer import date_values, money_values, percentage_values, time_values


def test_money_values() -> None:
    """逗号和万元单位应统一为元。"""
    assert money_values("总价 ¥603,000.00") == {Decimal("603000.00")}
    assert money_values("预算 60.3万元") == {Decimal("603000.0")}


def test_time_date_and_percentage_values() -> None:
    """关键数值应保留单位语义。"""
    assert "15日" in time_values("验收后15日内付款")
    assert date_values("2026年4月1日至2027年3月31日") == {"2026-04-01", "2027-03-31"}
    assert percentage_values("预付30%，违约金5%") == {Decimal("30"), Decimal("5")}
