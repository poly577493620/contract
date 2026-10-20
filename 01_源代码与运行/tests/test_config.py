"""工作区写入边界。"""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from smart_compliance.config import ModelEndpoint, Settings


def test_relative_output_is_inside_project() -> None:
    """相对路径应解析到项目根目录。"""
    settings = Settings()
    resolved = settings.writable_path(Path("outputs/test.json"))
    assert resolved.is_relative_to(settings.root)


def test_outside_output_is_rejected() -> None:
    """项目外绝对写入路径必须被拒绝。"""
    settings = Settings()
    outside_path = settings.root.parent / "outside-test.json"
    with pytest.raises(ValueError, match="写入路径必须位于项目目录内"):
        settings.writable_path(outside_path)


def test_glm_53_cannot_disable_thinking() -> None:
    """当前官方能力不允许 GLM-5.3 系列关闭 thinking。"""
    with pytest.raises(ValidationError, match="不支持关闭 thinking"):
        ModelEndpoint(
            name="glm",
            provider="test",
            model="glm-5.3-flash",
            base_url="https://example.invalid",
            api_key=SecretStr("test"),
            thinking="disabled",
        )


def test_unknown_model_cannot_receive_reasoning_controls() -> None:
    """非白名单模型不得透传推理参数。"""
    with pytest.raises(ValidationError, match="不在 thinking 参数白名单"):
        ModelEndpoint(
            name="unknown",
            provider="test",
            model="unknown-model",
            base_url="https://example.invalid",
            api_key=SecretStr("test"),
            thinking="enabled",
        )
