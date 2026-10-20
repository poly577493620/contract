"""应用配置与工作区边界。"""

from datetime import date
from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def project_root() -> Path:
    """返回项目根目录。

    Returns:
        包含 pyproject.toml 的项目目录。
    """
    return Path(__file__).resolve().parents[2]


def portable_project_path(path: str | Path, *, root: Path | None = None) -> str:
    """把审计文件位置转换为不暴露本机目录的可移植路径。

    项目内文件返回相对项目根目录的 POSIX 路径；项目外文件只保留文件名。
    这样报告仍能定位审计附件，同时不会写入盘符、用户名或本机工作区。

    Args:
        path: 待公开的文件路径。
        root: 可选项目根目录，主要用于测试。

    Returns:
        可安全写入报告的相对路径或文件名。
    """
    selected_root = (root or project_root()).resolve()
    candidate = Path(path)
    try:
        resolved = candidate.resolve() if candidate.is_absolute() else (selected_root / candidate).resolve()
        return resolved.relative_to(selected_root).as_posix()
    except (OSError, ValueError):
        return candidate.name


class ModelEndpoint(BaseModel):
    """OpenAI-compatible 模型端点配置。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: SecretStr
    api_path: str = Field(default="/v1/chat/completions", min_length=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    response_format: Literal["json_object", "none"] = "json_object"
    max_output_tokens: int = Field(default=8192, ge=128, le=32768)
    timeout_seconds: float | None = Field(default=None, gt=0, le=600)
    max_retries: int | None = Field(default=None, ge=0, le=5)
    thinking: Literal["enabled", "disabled", "none"] = "none"
    reasoning_effort: Literal["low", "high", "max"] | None = None
    price_input_per_million: float | None = Field(default=None, ge=0)
    price_output_per_million: float | None = Field(default=None, ge=0)
    price_currency: str | None = Field(default=None, min_length=3, max_length=8)
    price_unit: Literal["per_million_tokens"] = "per_million_tokens"
    price_source_url: str | None = None
    price_verified_at: date | None = None

    @property
    def request_temperature(self) -> float:
        """返回端点显式温度或已验证模型能力默认值。"""
        if self.temperature is not None:
            return self.temperature
        return 1.0 if self.model == "kimi-k3" else 0.0

    @property
    def request_thinking(self) -> Literal["enabled", "disabled"] | None:
        """返回经过模型能力白名单约束的 thinking 参数。"""
        if self.thinking != "none":
            return self.thinking
        if self.model in {"glm-5.3", "glm-5.3-flash"}:
            return "enabled"
        if self.model in {"deepseek-v4-flash", "deepseek-v4-pro"}:
            return "disabled"
        return None

    @property
    def request_reasoning_effort(self) -> Literal["low", "high", "max"] | None:
        """返回端点显式推理强度或 GLM-5.3 系列的低强度默认值。"""
        if self.reasoning_effort is not None:
            return self.reasoning_effort
        if self.model in {"glm-5.3", "glm-5.3-flash"}:
            return "low"
        return None

    @model_validator(mode="after")
    def validate_reasoning_controls(self) -> "ModelEndpoint":
        """拒绝已知不受支持或彼此冲突的推理参数组合。"""
        reasoning_models = {"glm-5.3", "glm-5.3-flash", "deepseek-v4-flash", "deepseek-v4-pro"}
        if self.thinking != "none" and self.model not in reasoning_models:
            raise ValueError(f"模型 {self.model} 不在 thinking 参数白名单中")
        if self.reasoning_effort is not None and self.model not in reasoning_models:
            raise ValueError(f"模型 {self.model} 不在 reasoning_effort 参数白名单中")
        if self.model in {"glm-5.3", "glm-5.3-flash"} and self.thinking == "disabled":
            raise ValueError("GLM-5.3/GLM-5.3-Flash 当前不支持关闭 thinking")
        if self.request_thinking == "disabled" and self.reasoning_effort is not None:
            raise ValueError("thinking=disabled 时不得同时设置 reasoning_effort")
        return self


class Settings(BaseSettings):
    """从环境变量与 `.env` 加载应用配置。"""

    model_config = SettingsConfigDict(
        env_prefix="SMART_COMPLIANCE_",
        env_file=project_root() / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    model_endpoints: str = "[]"
    request_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    max_retries: int = Field(default=2, ge=0, le=5)
    max_upload_mb: int = Field(default=25, ge=1, le=200)
    max_pdf_pages: int = Field(default=200, ge=1, le=2000)
    log_level: str = "INFO"
    low_confidence_threshold: float = Field(default=0.72, ge=0, le=1)
    ocr_cache_dir: Path = Path("outputs/ocr-cache")
    ocr_default_dpi: int = Field(default=300, ge=150, le=600)
    ocr_retry_dpi: int = Field(default=360, ge=150, le=600)
    ocr_min_text_score: float = Field(default=0.50, ge=0, le=1)
    ocr_low_confidence_score: float = Field(default=0.85, ge=0, le=1)
    ocr_min_median_confidence: float = Field(default=0.85, ge=0, le=1)
    ocr_max_low_confidence_ratio: float = Field(default=0.15, ge=0, le=1)
    ocr_max_fragment_line_ratio: float = Field(default=0.35, ge=0, le=1)

    @cached_property
    def endpoints(self) -> list[ModelEndpoint]:
        """解析模型端点 JSON。

        Returns:
            已验证的模型端点列表。
        """
        return TypeAdapter(list[ModelEndpoint]).validate_json(self.model_endpoints)

    @property
    def root(self) -> Path:
        """返回项目根目录。

        Returns:
            规范化的项目根目录。
        """
        return project_root()

    def writable_path(self, path: Path) -> Path:
        """验证写入路径位于项目目录内。

        Args:
            path: 待验证路径，可为相对路径或绝对路径。

        Returns:
            规范化的绝对路径。

        Raises:
            ValueError: 路径越过项目边界。
        """
        candidate = path if path.is_absolute() else self.root / path
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            message = f"写入路径必须位于项目目录内: {resolved}"
            raise ValueError(message)
        return resolved
