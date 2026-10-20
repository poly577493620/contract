"""OpenAI-compatible Chat Completions provider。"""

import asyncio
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any, cast

import httpx
import structlog

from smart_compliance.config import ModelEndpoint, Settings
from smart_compliance.domain.exceptions import ModelInvocationError
from smart_compliance.llm.base import InvocationAttempt, LlmResponse

log = structlog.get_logger()


class OpenAiCompatibleProvider:
    """通过异步 HTTP 调用兼容 Chat Completions 的模型。"""

    def __init__(self, endpoint: ModelEndpoint, settings: Settings) -> None:
        """初始化 provider。"""
        self._endpoint = endpoint
        self._settings = settings

    @property
    def name(self) -> str:
        """返回端点配置名称。"""
        return self._endpoint.name

    @property
    def provider_name(self) -> str:
        """返回供应商显示名称。"""
        return self._endpoint.provider

    @property
    def model_name(self) -> str:
        """返回模型精确名称。"""
        return self._endpoint.model

    async def complete(self, messages: list[dict[str, str]]) -> LlmResponse:
        """调用模型并记录延迟、token、重试和脱敏响应元数据。"""
        url = _endpoint_url(self._endpoint.base_url, self._endpoint.api_path)
        headers = {"Authorization": f"Bearer {self._endpoint.api_key.get_secret_value()}"}
        payload = _request_payload(self._endpoint, messages)
        started = time.perf_counter()
        try:
            result = await self._request_with_retry(url, headers, payload)
        except ModelInvocationError as error:
            error.elapsed_ms = int((time.perf_counter() - started) * 1000)
            raise
        latency_ms = int((time.perf_counter() - started) * 1000)
        return self._parse_response(result, latency_ms)

    def _parse_response(self, result: "_RequestResult", latency_ms: int) -> LlmResponse:
        data = result.data
        try:
            choices = cast(list[dict[str, Any]], data["choices"])
            choice = choices[0]
            message = cast(dict[str, Any], choice["message"])
        except (KeyError, IndexError, TypeError) as error:
            invocation_error = ModelInvocationError(
                "模型响应缺少 choices[0].message。",
                category="invalid_response",
                retry_count=result.retry_count,
                elapsed_ms=latency_ms,
            )
            raise _attach_audit(
                invocation_error, {"response_keys": _key_list(data)}, _attempt_dicts(result.attempts)
            ) from error
        content_value = message.get("content")
        content = content_value if isinstance(content_value, str) else ""
        usage = cast(dict[str, Any], data.get("usage", {}))
        input_tokens = _optional_int(usage.get("prompt_tokens"))
        output_tokens = _optional_int(usage.get("completion_tokens"))
        finish_reason = str(choice["finish_reason"]) if choice.get("finish_reason") is not None else None
        metadata = _response_metadata(data, message, usage, finish_reason, content)
        if not content.strip():
            category = "empty_content_length" if finish_reason == "length" else "empty_content"
            invocation_error = ModelInvocationError(
                f"模型响应 content 为空（finish_reason={finish_reason or 'unknown'}, output_tokens={output_tokens}）。",
                category=category,
                retry_count=result.retry_count,
                elapsed_ms=latency_ms,
            )
            raise _attach_audit(invocation_error, cast(dict[str, object], metadata), _attempt_dicts(result.attempts))
        return LlmResponse(
            content=content,
            provider_name=self._endpoint.provider,
            model_name=self._endpoint.model,
            api_path=self._endpoint.api_path,
            response_id=str(data["id"]) if data.get("id") is not None else None,
            finish_reason=finish_reason,
            retry_count=result.retry_count,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=_estimate_cost(self._endpoint, input_tokens, output_tokens),
            attempts=result.attempts,
            response_metadata=metadata,
        )

    async def _request_with_retry(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> "_RequestResult":
        timeout_seconds = self._endpoint.timeout_seconds or self._settings.request_timeout_seconds
        max_retries = self._endpoint.max_retries
        if max_retries is None:
            max_retries = self._settings.max_retries
        attempts: list[InvocationAttempt] = []
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds)) as client:
            for attempt_index in range(max_retries + 1):
                try:
                    response = await client.post(url, headers=headers, json=payload)
                except httpx.TimeoutException as error:
                    if await self._retry_transport("timeout", attempt_index, max_retries, attempts):
                        continue
                    raise _transport_error("timeout", attempt_index, attempts, error) from error
                except httpx.NetworkError as error:
                    if await self._retry_transport("network_error", attempt_index, max_retries, attempts):
                        continue
                    raise _transport_error("network_error", attempt_index, attempts, error) from error
                except httpx.TransportError as error:
                    if await self._retry_transport("transport_error", attempt_index, max_retries, attempts):
                        continue
                    raise _transport_error("transport_error", attempt_index, attempts, error) from error
                if _is_transient_status(response.status_code):
                    retrying = attempt_index < max_retries
                    delay = _retry_delay(response, attempt_index) if retrying else None
                    attempts.append(
                        InvocationAttempt(
                            attempt=attempt_index + 1,
                            outcome="http_error",
                            status_code=response.status_code,
                            retrying=retrying,
                            retry_delay_seconds=delay,
                        )
                    )
                    if retrying and delay is not None:
                        log.warning(
                            "model_request_retry",
                            provider=self.provider_name,
                            model=self.model_name,
                            attempt=attempt_index + 1,
                            status_code=response.status_code,
                            delay_seconds=delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                try:
                    response.raise_for_status()
                    data = cast(dict[str, Any], response.json())
                except httpx.HTTPStatusError as error:
                    raise _http_error(response, attempt_index, attempts) from error
                except ValueError as error:
                    invocation_error = ModelInvocationError(
                        "模型接口返回的不是有效 JSON。",
                        category="invalid_response",
                        status_code=response.status_code,
                        retry_count=attempt_index,
                    )
                    raise _attach_audit(invocation_error, {}, _attempt_dicts(attempts)) from error
                attempts.append(
                    InvocationAttempt(
                        attempt=attempt_index + 1,
                        outcome="success",
                        status_code=response.status_code,
                        retrying=False,
                    )
                )
                return _RequestResult(data, attempt_index, attempts)
        raise ModelInvocationError("模型请求未产生响应。", category="no_response")

    async def _retry_transport(
        self,
        outcome: str,
        attempt_index: int,
        max_retries: int,
        attempts: list[InvocationAttempt],
    ) -> bool:
        retrying = attempt_index < max_retries
        delay = _exponential_delay(attempt_index) if retrying else None
        attempts.append(
            InvocationAttempt(
                attempt=attempt_index + 1,
                outcome=cast(Any, outcome),
                retrying=retrying,
                retry_delay_seconds=delay,
            )
        )
        if retrying and delay is not None:
            log.warning(
                "model_transport_retry",
                provider=self.provider_name,
                model=self.model_name,
                attempt=attempt_index + 1,
                category=outcome,
                delay_seconds=delay,
            )
            await asyncio.sleep(delay)
        return retrying


@dataclass(frozen=True, slots=True)
class _RequestResult:
    data: dict[str, Any]
    retry_count: int
    attempts: list[InvocationAttempt]


def _request_payload(endpoint: ModelEndpoint, messages: list[dict[str, str]]) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": endpoint.model,
        "messages": messages,
        "temperature": endpoint.request_temperature,
        "max_tokens": endpoint.max_output_tokens,
    }
    if endpoint.response_format == "json_object":
        payload["response_format"] = {"type": "json_object"}
    thinking = endpoint.request_thinking
    if thinking is not None:
        payload["thinking"] = {"type": thinking}
    reasoning_effort = endpoint.request_reasoning_effort
    if reasoning_effort is not None:
        payload["reasoning_effort"] = reasoning_effort
    return payload


def _endpoint_url(base_url: str, api_path: str) -> str:
    normalized = base_url.rstrip("/")
    path = "/" + api_path.strip("/")
    if normalized.endswith("/v1") and path.startswith("/v1/"):
        path = path.removeprefix("/v1")
    return normalized + path


def _is_transient_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code <= 599


def _retry_delay(response: httpx.Response, attempt_index: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        parsed = _parse_retry_after(retry_after)
        if parsed is not None:
            return min(parsed, 60.0)
    return _exponential_delay(attempt_index)


def _parse_retry_after(value: str) -> float | None:
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())


def _exponential_delay(attempt_index: int) -> float:
    base = min(float(2**attempt_index), 8.0)
    return round(base + random.uniform(0.0, min(base * 0.25, 1.0)), 3)


def _transport_error(
    category: str,
    retry_count: int,
    attempts: list[InvocationAttempt],
    error: httpx.TransportError,
) -> ModelInvocationError:
    messages = {
        "timeout": "模型请求超时。",
        "network_error": "模型网络连接失败。",
        "transport_error": "模型传输连接被远端中断。",
    }
    invocation_error = ModelInvocationError(
        messages[category],
        category=category,
        retry_count=retry_count,
    )
    return _attach_audit(invocation_error, {"exception_type": type(error).__name__}, _attempt_dicts(attempts))


def _http_error(
    response: httpx.Response,
    retry_count: int,
    attempts: list[InvocationAttempt],
) -> ModelInvocationError:
    body = response.text.lower()
    if response.status_code == 403 and ("explicit opt in" in body or "hosted in china" in body):
        category = "account_opt_in_required"
        message = "模型需要在供应商工作区显式启用。"
    else:
        category = {
            401: "authentication_error",
            403: "permission_denied",
            429: "rate_limit",
        }.get(response.status_code, "provider_server_error" if response.status_code >= 500 else "http_error")
        message = f"模型接口返回 HTTP {response.status_code}。"
    invocation_error = ModelInvocationError(
        message,
        category=category,
        status_code=response.status_code,
        retry_count=retry_count,
    )
    return _attach_audit(
        invocation_error,
        {"response_content_type": response.headers.get("content-type")},
        _attempt_dicts(attempts),
    )


def _response_metadata(
    data: dict[str, Any],
    message: dict[str, Any],
    usage: dict[str, Any],
    finish_reason: str | None,
    content: str,
) -> dict[str, str | int | bool | None]:
    reasoning = message.get("reasoning_content")
    details = usage.get("completion_tokens_details")
    reasoning_tokens = (
        _optional_int(cast(dict[str, Any], details).get("reasoning_tokens")) if isinstance(details, dict) else None
    )
    return {
        "finish_reason": finish_reason,
        "response_keys": _key_list(data),
        "message_keys": _key_list(message),
        "reasoning_content_present": isinstance(reasoning, str) and bool(reasoning),
        "reasoning_content_characters": len(reasoning) if isinstance(reasoning, str) else 0,
        "reasoning_tokens": reasoning_tokens,
        "content_characters": len(content),
    }


def _key_list(payload: dict[str, Any]) -> str:
    return ",".join(sorted(str(key) for key in payload))


def _attempt_dicts(attempts: list[InvocationAttempt]) -> list[dict[str, object]]:
    return [cast(dict[str, object], attempt.model_dump(mode="json")) for attempt in attempts]


def _attach_audit(
    error: ModelInvocationError,
    diagnostics: dict[str, object],
    attempts: list[dict[str, object]],
) -> ModelInvocationError:
    error.diagnostics = diagnostics
    error.attempts = attempts
    return error


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _estimate_cost(endpoint: ModelEndpoint, input_tokens: int | None, output_tokens: int | None) -> float | None:
    if (
        input_tokens is None
        or output_tokens is None
        or endpoint.price_input_per_million is None
        or endpoint.price_output_per_million is None
    ):
        return None
    cost = Decimal(input_tokens) * Decimal(str(endpoint.price_input_per_million)) / Decimal(1_000_000)
    cost += Decimal(output_tokens) * Decimal(str(endpoint.price_output_per_million)) / Decimal(1_000_000)
    return float(cost)
