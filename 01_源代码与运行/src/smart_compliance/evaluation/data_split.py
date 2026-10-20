"""训练集与盲测集边界。"""

from smart_compliance.domain.exceptions import BlindSetViolationError

TRAINING_RANGE = range(1, 21)
BLIND_RANGE = range(21, 31)


def validate_gold_sample(sample_id: str) -> None:
    """禁止把 021-030 当作带标签样本。

    Args:
        sample_id: 三位样本编号。

    Raises:
        BlindSetViolationError: 样本属于盲测集或编号非法。
    """
    if not sample_id.isdigit():
        raise BlindSetViolationError(f"样本编号不是数字: {sample_id}")
    numeric = int(sample_id)
    if numeric not in TRAINING_RANGE:
        raise BlindSetViolationError(f"样本 {sample_id} 不属于可使用参考答案的 001-020 训练集。")


def parse_training_samples(expression: str) -> list[str]:
    """解析逗号分隔编号或 `001-020` 范围。"""
    values: list[int] = []
    for token in expression.split(","):
        stripped = token.strip()
        if "-" in stripped:
            start, end = (int(value) for value in stripped.split("-", maxsplit=1))
            values.extend(range(start, end + 1))
        elif stripped:
            values.append(int(stripped))
    sample_ids = [f"{value:03d}" for value in sorted(set(values))]
    for sample_id in sample_ids:
        validate_gold_sample(sample_id)
    return sample_ids
