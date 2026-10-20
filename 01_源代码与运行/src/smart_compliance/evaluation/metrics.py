"""Precision、Recall、F1 计算。"""

from collections.abc import Hashable, Iterable

from smart_compliance.domain.models import MetricResult


def classification_metrics(predictions: Iterable[Hashable], gold: Iterable[Hashable]) -> MetricResult:
    """按集合匹配计算问题级指标。

    Args:
        predictions: 预测问题主键。
        gold: 真实问题主键。

    Returns:
        TP/FP/FN 与 Precision/Recall/F1。
    """
    predicted_set = set(predictions)
    gold_set = set(gold)
    true_positive = len(predicted_set & gold_set)
    false_positive = len(predicted_set - gold_set)
    false_negative = len(gold_set - predicted_set)
    precision = _safe_divide(true_positive, true_positive + false_positive)
    recall = _safe_divide(true_positive, true_positive + false_negative)
    f1 = _safe_divide(2 * precision * recall, precision + recall)
    return MetricResult(
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        precision=precision,
        recall=recall,
        f1=f1,
    )


def _safe_divide(numerator: float, denominator: float) -> float:
    return 0.0 if denominator == 0 else numerator / denominator
