"""评测失败记录与重复运行多数票。"""

from pathlib import Path

from smart_compliance.domain.exceptions import ModelInvocationError
from smart_compliance.domain.models import PredictionIssue, Verdict
from smart_compliance.evaluation import runner


class FakeProvider:
    """提供失败记录所需的安全显示属性。"""

    provider_name = "test-provider"
    model_name = "test-model"


def test_failed_model_run_keeps_elapsed_and_does_not_raise(tmp_path: Path) -> None:
    """单次模型失败应转换为 success=false 的审计记录。"""
    context = runner._FailedRunContext(FakeProvider(), "005", "endpoint-a", 2, tmp_path / "raw.txt")
    error = ModelInvocationError(
        "模型请求超时。",
        category="timeout",
        retry_count=1,
        elapsed_ms=12_345,
    )
    record = runner._failed_run(context, error)
    assert record.success is False
    assert record.error_category == "timeout"
    assert record.retry_count == 1
    assert record.latency_ms == 12_345


def test_failed_run_uses_portable_raw_output_path(tmp_path: Path) -> None:
    """评测清单不得写入项目外绝对目录。"""
    raw_path = tmp_path / "raw" / "005-r1.txt"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text("{}", encoding="utf-8")
    context = runner._FailedRunContext(FakeProvider(), "005", "endpoint-a", 1, raw_path)
    record = runner._failed_run(context, RuntimeError("invalid output"))
    assert record.raw_output_file is not None
    assert record.raw_output_file.endswith("raw/005-r1.txt")
    assert not Path(record.raw_output_file).is_absolute()


def test_consensus_requires_majority_of_repetitions() -> None:
    """三次运行只出现一次的问题不得进入多数票结果。"""
    common = PredictionIssue(
        sample_id="005",
        issue_type=Verdict.DIFFERENT,
        clause_code="payment_terms",
        item_id="a",
    )
    second_vote = common.model_copy(update={"item_id": "b"})
    outlier = PredictionIssue(
        sample_id="005",
        issue_type=Verdict.MISSING,
        clause_code="service_scope",
        item_id="c",
    )
    selected = runner._consensus_predictions([common, second_vote, outlier], repetitions=3)
    assert len(selected) == 1
    assert selected[0].clause_code == "payment_terms"
