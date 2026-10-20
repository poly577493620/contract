"""Web 会话清理边界和文件角色提示。"""

from pathlib import Path

import pytest

from smart_compliance.config import Settings
from smart_compliance.ui.session import (
    clear_current_session,
    read_session_text,
    role_mismatch_warnings,
    session_directory,
)


def _settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    monkeypatch.setattr("smart_compliance.config.project_root", lambda: tmp_path)
    return Settings(model_endpoints="[]")


def test_role_mismatch_warning_is_non_empty() -> None:
    """两个上传区疑似放反时给出中文提示。"""
    warnings = role_mismatch_warnings("某项目_采购合同.pdf", "某项目_采购结果.pdf")
    assert len(warnings) == 2
    assert all("放反" in warning for warning in warnings)


def test_clear_only_exact_current_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """清理功能只能删除 32 位会话 ID 对应的具体目录。"""
    settings = _settings(monkeypatch, tmp_path)
    session_id = "a" * 32
    target = session_directory(settings, session_id)
    target.mkdir(parents=True)
    (target / "review.json").write_text("{}", encoding="utf-8")
    assert clear_current_session(settings, session_id, target) == target
    assert not target.exists()


def test_clear_rejects_mismatched_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """显示路径被篡改时不得删除会话根目录或其他会话。"""
    settings = _settings(monkeypatch, tmp_path)
    session_id = "b" * 32
    other = session_directory(settings, "c" * 32)
    other.mkdir(parents=True)
    with pytest.raises(ValueError, match="不一致"):
        clear_current_session(settings, session_id, other)
    assert other.exists()


def test_audit_text_cannot_escape_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """界面不得借原始输出引用读取当前会话目录外的文件。"""
    settings = _settings(monkeypatch, tmp_path)
    target = session_directory(settings, "d" * 32)
    target.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    assert "不在当前会话目录" in read_session_text(target, outside)
