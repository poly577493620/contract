"""LLM provider 协议与响应模型。"""

from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class InvocationAttempt(BaseModel):
    """一次脱敏的传输尝试记录。"""

    model_config = ConfigDict(extra="forbid")

    attempt: int = Field(ge=1)
    outcome: Literal["success", "http_error", "timeout", "network_error", "transport_error"]
    status_code: int | None = Field(default=None, ge=100, le=599)
    retrying: bool
    retry_delay_seconds: float | None = Field(default=None, ge=0)


class LlmResponse(BaseModel):
    """统一模型响应。"""

    model_config = ConfigDict(extra="forbid")

    content: str
    provider_name: str
    model_name: str
    api_path: str
    response_id: str | None = None
    finish_reason: str | None = None
    retry_count: int = Field(default=0, ge=0)
    invoked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)
    attempts: list[InvocationAttempt] = Field(default_factory=list)
    response_metadata: dict[str, str | int | bool | None] = Field(default_factory=dict)


class LlmProvider(Protocol):
    """模型 provider 最小协议。"""

    @property
    def name(self) -> str:
        """返回配置名称。"""
        ...

    @property
    def provider_name(self) -> str:
        """返回供应商显示名称。"""
        ...

    @property
    def model_name(self) -> str:
        """返回模型精确名称。"""
        ...

    async def complete(self, messages: list[dict[str, str]]) -> LlmResponse:
        """执行一次结构化质检请求。"""
        ...
