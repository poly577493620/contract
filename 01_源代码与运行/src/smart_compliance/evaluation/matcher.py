"""预测与 gold 的透明问题级匹配。"""

from smart_compliance.domain.models import GoldIssue, MetricResult, PredictionIssue, ReviewReport, Verdict
from smart_compliance.evaluation.metrics import classification_metrics

IssueKey = tuple[str, Verdict, str]


def prediction_issues(report: ReviewReport) -> list[PredictionIssue]:
    """从报告提取缺失项和差异项预测。"""
    if report.sample_id is None:
        return []
    return [
        PredictionIssue(
            sample_id=report.sample_id,
            issue_type=item.verdict,
            clause_code=item.clause_code,
            item_id=item.item_id,
        )
        for item in report.items
        if item.verdict in {Verdict.MISSING, Verdict.DIFFERENT}
    ]


def issue_key(issue: GoldIssue | PredictionIssue) -> IssueKey:
    """构建公开、稳定的问题级主键。"""
    return issue.sample_id, issue.issue_type, issue.clause_code


def metrics_for(
    predictions: list[PredictionIssue],
    gold: list[GoldIssue],
    verdict: Verdict | None = None,
) -> MetricResult:
    """按可选问题类别计算指标。"""
    selected_predictions = [issue for issue in predictions if verdict is None or issue.issue_type == verdict]
    selected_gold = [issue for issue in gold if verdict is None or issue.issue_type == verdict]
    return classification_metrics(map(issue_key, selected_predictions), map(issue_key, selected_gold))
