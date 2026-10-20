"""页面图像视觉转录 Gold 的结构与安全边界。"""

from pathlib import Path

import pytest

from smart_compliance.evaluation.visual_gold import load_visual_transcription, validate_visual_transcription


def _transcription_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "annotations" / "visual-reference-transcription-v1.json"


def test_visual_transcription_covers_all_reference_samples_and_30_issues() -> None:
    """视觉版必须完整覆盖 001-020，并保持页面上列出的 30 条问题。"""
    transcription = load_visual_transcription(_transcription_path())
    validate_visual_transcription(transcription)
    assert len(transcription.samples) == 20
    assert sum(len(sample.issues) for sample in transcription.samples) == 30
    assert [sample.sample_id for sample in transcription.samples if not sample.issues] == ["011", "014"]


def test_visual_transcription_keeps_006_as_one_reference_item() -> None:
    """006 页面把培训和应急响应列在同一个增值服务缺失项中。"""
    transcription = load_visual_transcription(_transcription_path())
    sample = next(item for item in transcription.samples if item.sample_id == "006")
    assert len(sample.issues) == 1
    assert "免费技术培训" in sample.issues[0].procurement_text
    assert "24小时应急响应" in sample.issues[0].procurement_text


def test_model_extra_is_separate_and_not_human_verified() -> None:
    """005 模型额外项不得混入参考答案候选，也不能冒充人工结论。"""
    transcription = load_visual_transcription(_transcription_path())
    sample = next(item for item in transcription.samples if item.sample_id == "005")
    assert [issue.clause_code for issue in sample.issues] == ["payment_terms"]
    assert [item.clause_code for item in transcription.arbitration_items] == ["contract_term"]
    assert not transcription.human_verified


def test_visual_transcription_cannot_claim_human_verification(tmp_path: Path) -> None:
    """AI 辅助转录即使被误改总状态，也必须被校验器拒绝。"""
    transcription = load_visual_transcription(_transcription_path())
    invalid = transcription.model_copy(update={"human_verified": True})
    with pytest.raises(ValueError, match="不能直接标记"):
        validate_visual_transcription(invalid)
