"""Streamlit 会话目录边界与文件角色提示。"""

import re
import shutil
from pathlib import Path

from smart_compliance.config import Settings

SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def role_mismatch_warnings(procurement_name: str, contract_name: str) -> list[str]:
    """根据文件名给出非阻断式角色疑似颠倒提示。"""
    warnings: list[str] = []
    procurement_lower = procurement_name.lower()
    contract_lower = contract_name.lower()
    if "合同" in procurement_name or "contract" in procurement_lower:
        warnings.append("采购结果上传区的文件名疑似为合同，请核对两个文件是否放反。")
    result_hints = ("采购结果", "中标", "成交", "result", "award")
    if any(hint in contract_lower for hint in result_hints):
        warnings.append("采购合同上传区的文件名疑似为采购结果，请核对两个文件是否放反。")
    return warnings


def session_directory(settings: Settings, session_id: str) -> Path:
    """返回严格限定在项目会话根目录内的会话路径。"""
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError("会话 ID 格式无效，拒绝访问会话目录。")
    return settings.writable_path(Path("outputs") / "sessions" / session_id)


def clear_current_session(settings: Settings, session_id: str, displayed_path: Path) -> Path:
    """仅删除与当前会话 ID 完全一致的具体目录。"""
    expected = session_directory(settings, session_id)
    resolved = settings.writable_path(displayed_path)
    if resolved != expected:
        raise ValueError("显示目录与当前会话不一致，拒绝清理。")
    if resolved.exists():
        if not resolved.is_dir():
            raise ValueError("当前会话目标不是目录，拒绝清理。")
        shutil.rmtree(resolved)
    return resolved


def read_session_text(output_dir: Path, target: Path, max_characters: int = 30_000) -> str:
    """在当前会话目录内读取有限长度的审计文本。"""
    root = output_dir.resolve()
    resolved = target.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        return "文件不存在或不在当前会话目录内。"
    content = resolved.read_text(encoding="utf-8")
    if len(content) <= max_characters:
        return content
    return content[:max_characters] + "\n……（界面仅展示前 30000 字符，请查看会话目录中的原文件）"
