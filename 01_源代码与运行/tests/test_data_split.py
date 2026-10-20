"""训练集与盲测集隔离。"""

import pytest

from smart_compliance.domain.exceptions import BlindSetViolationError
from smart_compliance.evaluation.data_split import parse_training_samples, validate_gold_sample


def test_parse_training_range() -> None:
    """训练集范围应格式化为三位编号。"""
    assert parse_training_samples("001-003,005") == ["001", "002", "003", "005"]


def test_blind_sample_cannot_use_gold() -> None:
    """021-030 不得进入带标签评测。"""
    with pytest.raises(BlindSetViolationError):
        validate_gold_sample("021")
