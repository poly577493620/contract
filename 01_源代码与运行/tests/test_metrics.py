"""评测指标边界。"""

import pytest

from smart_compliance.evaluation.metrics import classification_metrics


def test_precision_recall_f1() -> None:
    """TP/FP/FN 口径应透明可复算。"""
    result = classification_metrics({"a", "b"}, {"a", "c"})
    assert result.true_positive == 1
    assert result.false_positive == 1
    assert result.false_negative == 1
    assert result.precision == pytest.approx(0.5)
    assert result.recall == pytest.approx(0.5)
    assert result.f1 == pytest.approx(0.5)


def test_zero_denominator_returns_zero() -> None:
    """无预测且无 gold 时指标按约定为 0。"""
    result = classification_metrics([], [])
    assert result.precision == 0
    assert result.recall == 0
    assert result.f1 == 0
