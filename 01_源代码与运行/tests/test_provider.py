"""模型 provider 的重试、鉴权和响应校验。"""

from types import TracebackType
from typing import Self

import httpx
import pytest
from pydantic import SecretStr

from smart_compliance.config import ModelEndpoint, Settings
from smart_compliance.domain.exceptions import ModelInvocationError
from smart_compliance.llm.openai_compatible import OpenAiCompatibleProvider


class StubAsyncClient:
    """按顺序返回预置响应的异步客户端。"""

    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self.responses = responses
        self.calls = 0
        self.last_headers: dict[str, str] = {}
        self.last_payload: dict[str, object] = {}
        self.last_url = ""

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]) -> httpx.Response:
        self.last_url = url
        self.last_headers = headers
        self.last_payload = json
        response_or_error = self.responses[self.calls]
        self.calls += 1
        if isinstance(response_or_error, Exception):
            raise response_or_error
        return response_or_error


def _endpoint() -> ModelEndpoint:
    return ModelEndpoint(
        name="test-model",
        provider="test",
        model="model-v1",
        base_url="https://example.invalid/v1",
        api_key=SecretStr("unit-test-secret"),
        price_input_per_million=1.0,
        price_output_per_million=2.0,
    )


def _response(
    status_code: int,
    payload: object | None = None,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    if content is not None:
        return httpx.Response(status_code, content=content, headers=headers, request=request)
    return httpx.Response(status_code, json=payload, headers=headers, request=request)


def _install_client(monkeypatch: pytest.MonkeyPatch, client: StubAsyncClient) -> None:
    def factory(**_kwargs: object) -> StubAsyncClient:
        return client

    monkeypatch.setattr(httpx, "AsyncClient", factory)


async def _no_sleep(_: float) -> None:
    return None


@pytest.mark.asyncio
async def test_retry_429_then_return_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """429 应有限重试，并保留 token 与真实配置成本。"""
    client = StubAsyncClient(
        [
            _response(429, {"error": "rate limit"}),
            _response(
                200,
                {
                    "choices": [{"message": {"content": "{}"}}],
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
                },
            ),
        ]
    )
    _install_client(monkeypatch, client)
    monkeypatch.setattr("smart_compliance.llm.openai_compatible.asyncio.sleep", _no_sleep)
    provider = OpenAiCompatibleProvider(_endpoint(), Settings(max_retries=1, request_timeout_seconds=1))
    result = await provider.complete([{"role": "user", "content": "test"}])
    assert client.calls == 2
    assert client.last_headers["Authorization"] == "Bearer unit-test-secret"
    assert result.input_tokens == 1000
    assert result.output_tokens == 500
    assert result.estimated_cost == pytest.approx(0.002)


@pytest.mark.asyncio
async def test_authentication_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """401 是配置/权限错误，不得盲目重试。"""
    client = StubAsyncClient([_response(401, {"error": "unauthorized"})])
    _install_client(monkeypatch, client)
    provider = OpenAiCompatibleProvider(_endpoint(), Settings(max_retries=2, request_timeout_seconds=1))
    with pytest.raises(ModelInvocationError, match="HTTP 401"):
        await provider.complete([{"role": "user", "content": "test"}])
    assert client.calls == 1


@pytest.mark.asyncio
async def test_invalid_json_response_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """成功状态但非法 JSON 不得进入业务结构校验。"""
    client = StubAsyncClient([_response(200, content=b"not-json")])
    _install_client(monkeypatch, client)
    provider = OpenAiCompatibleProvider(_endpoint(), Settings(max_retries=0, request_timeout_seconds=1))
    with pytest.raises(ModelInvocationError, match="不是有效 JSON"):
        await provider.complete([{"role": "user", "content": "test"}])


@pytest.mark.asyncio
async def test_missing_content_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺少 choices 内容的响应必须明确失败。"""
    client = StubAsyncClient([_response(200, {"choices": []})])
    _install_client(monkeypatch, client)
    provider = OpenAiCompatibleProvider(_endpoint(), Settings(max_retries=0, request_timeout_seconds=1))
    with pytest.raises(ModelInvocationError, match="缺少 choices"):
        await provider.complete([{"role": "user", "content": "test"}])


@pytest.mark.asyncio
async def test_empty_content_is_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    """供应商返回空正文时应给出独立错误类别。"""
    client = StubAsyncClient([_response(200, {"choices": [{"message": {"content": ""}}]})])
    _install_client(monkeypatch, client)
    provider = OpenAiCompatibleProvider(_endpoint(), Settings(max_retries=0, request_timeout_seconds=1))
    with pytest.raises(ModelInvocationError) as captured:
        await provider.complete([{"role": "user", "content": "test"}])
    assert captured.value.category == "empty_content"


@pytest.mark.asyncio
async def test_empty_length_is_not_retried_and_keeps_safe_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    """成功但正文为空且 length 截断时不得用相同请求盲重试。"""
    client = StubAsyncClient(
        [
            _response(
                200,
                {
                    "choices": [{"message": {"content": "", "reasoning_content": "hidden"}, "finish_reason": "length"}],
                    "usage": {"completion_tokens": 4096},
                },
            )
        ]
    )
    _install_client(monkeypatch, client)
    provider = OpenAiCompatibleProvider(_endpoint(), Settings(max_retries=2, request_timeout_seconds=1))
    with pytest.raises(ModelInvocationError) as captured:
        await provider.complete([{"role": "user", "content": "test"}])
    assert client.calls == 1
    assert captured.value.category == "empty_content_length"
    assert captured.value.diagnostics["reasoning_content_characters"] == 6
    assert "hidden" not in str(captured.value.diagnostics)


@pytest.mark.asyncio
async def test_kimi_capabilities_control_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kimi 默认温度为 1，且端点可关闭 response_format。"""
    endpoint = ModelEndpoint(
        name="kimi",
        provider="OpenCode Go",
        model="kimi-k3",
        base_url="https://example.invalid/zen/go",
        api_key=SecretStr("unit-test-secret"),
        response_format="none",
        max_output_tokens=1024,
    )
    client = StubAsyncClient([_response(200, {"choices": [{"message": {"content": "{}"}}]})])
    _install_client(monkeypatch, client)
    await OpenAiCompatibleProvider(endpoint, Settings(max_retries=0)).complete([{"role": "user", "content": "x"}])
    assert client.last_payload["temperature"] == 1.0
    assert client.last_payload["max_tokens"] == 1024
    assert "response_format" not in client.last_payload
    assert "thinking" not in client.last_payload
    assert "reasoning_effort" not in client.last_payload
    assert client.last_url.endswith("/v1/chat/completions")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "thinking", "reasoning_effort"),
    [
        ("glm-5.3-flash", "enabled", "low"),
        ("deepseek-v4-flash", "disabled", None),
    ],
)
async def test_reasoning_capabilities_control_payload(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    thinking: str,
    reasoning_effort: str | None,
) -> None:
    """GLM 和 DeepSeek 只接收各自当前官方支持的推理参数。"""
    endpoint = ModelEndpoint(
        name="reasoning-model",
        provider="OpenCode Go",
        model=model,
        base_url="https://example.invalid/zen/go",
        api_key=SecretStr("unit-test-secret"),
    )
    client = StubAsyncClient([_response(200, {"choices": [{"message": {"content": "{}"}}]})])
    _install_client(monkeypatch, client)
    await OpenAiCompatibleProvider(endpoint, Settings(max_retries=0)).complete([{"role": "user", "content": "x"}])
    assert client.last_payload["thinking"] == {"type": thinking}
    if reasoning_effort is None:
        assert "reasoning_effort" not in client.last_payload
    else:
        assert client.last_payload["reasoning_effort"] == reasoning_effort


@pytest.mark.asyncio
async def test_retry_503_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """503 只做有限的传输级重试，并保留尝试记录。"""
    client = StubAsyncClient(
        [
            _response(503, {"error": "busy"}),
            _response(200, {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}),
        ]
    )
    _install_client(monkeypatch, client)
    monkeypatch.setattr("smart_compliance.llm.openai_compatible.asyncio.sleep", _no_sleep)
    result = await OpenAiCompatibleProvider(_endpoint(), Settings(max_retries=1)).complete(
        [{"role": "user", "content": "x"}]
    )
    assert client.calls == 2
    assert result.retry_count == 1
    assert [attempt.outcome for attempt in result.attempts] == ["http_error", "success"]


@pytest.mark.asyncio
async def test_retry_after_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    """429 的 Retry-After 秒数优先于本地退避。"""
    delays: list[float] = []

    async def capture_sleep(delay: float) -> None:
        delays.append(delay)

    client = StubAsyncClient(
        [
            _response(429, {"error": "busy"}, headers={"Retry-After": "3"}),
            _response(200, {"choices": [{"message": {"content": "{}"}}]}),
        ]
    )
    _install_client(monkeypatch, client)
    monkeypatch.setattr("smart_compliance.llm.openai_compatible.asyncio.sleep", capture_sleep)
    await OpenAiCompatibleProvider(_endpoint(), Settings(max_retries=1)).complete([{"role": "user", "content": "x"}])
    assert delays == [3.0]


@pytest.mark.asyncio
async def test_china_model_opt_in_error_is_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    """中国部署模型未启用时不得误报为密钥错误或重试。"""
    client = StubAsyncClient(
        [_response(403, {"error": "Models hosted in China require explicit opt in for this workspace"})]
    )
    _install_client(monkeypatch, client)
    provider = OpenAiCompatibleProvider(_endpoint(), Settings(max_retries=2))
    with pytest.raises(ModelInvocationError) as captured:
        await provider.complete([{"role": "user", "content": "test"}])
    assert captured.value.category == "account_opt_in_required"
    assert captured.value.status_code == 403
    assert client.calls == 1


@pytest.mark.asyncio
async def test_timeout_exhaustion_records_retry_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """超时耗尽后记录安全错误类别和实际重试次数。"""
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    client = StubAsyncClient(
        [httpx.ReadTimeout("timeout", request=request), httpx.ReadTimeout("timeout", request=request)]
    )
    _install_client(monkeypatch, client)
    monkeypatch.setattr("smart_compliance.llm.openai_compatible.asyncio.sleep", _no_sleep)
    provider = OpenAiCompatibleProvider(_endpoint(), Settings(max_retries=1, request_timeout_seconds=1))
    with pytest.raises(ModelInvocationError) as captured:
        await provider.complete([{"role": "user", "content": "test"}])
    assert captured.value.category == "timeout"
    assert captured.value.retry_count == 1
    assert captured.value.elapsed_ms is not None


@pytest.mark.asyncio
async def test_remote_disconnect_is_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    """远端未返回响应即断连时不得泄露 httpx 调用堆栈。"""
    client = StubAsyncClient([httpx.RemoteProtocolError("server disconnected")])
    _install_client(monkeypatch, client)
    provider = OpenAiCompatibleProvider(_endpoint(), Settings(max_retries=0, request_timeout_seconds=1))
    with pytest.raises(ModelInvocationError) as captured:
        await provider.complete([{"role": "user", "content": "test"}])
    assert captured.value.category == "transport_error"
    assert str(captured.value) == "模型传输连接被远端中断。"
