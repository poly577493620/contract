"""在不输出 API Key 的前提下更新第三阶段三个既有端点参数。"""

import json
from pathlib import Path

import click


def configure(env_path: Path) -> list[str]:
    """更新 `.env` 中的模型 JSON，并返回处理过的端点名称。"""
    lines = env_path.read_text(encoding="utf-8").splitlines()
    prefix = "SMART_COMPLIANCE_MODEL_ENDPOINTS="
    selected_index = next((index for index, line in enumerate(lines) if line.startswith(prefix)), None)
    if selected_index is None:
        raise ValueError(".env 缺少 SMART_COMPLIANCE_MODEL_ENDPOINTS")
    raw_value = lines[selected_index].removeprefix(prefix).strip()
    quote = raw_value[:1]
    if quote in {"'", '"'} and raw_value.endswith(quote):
        raw_value = raw_value[1:-1]
    endpoints = json.loads(raw_value)
    if not isinstance(endpoints, list):
        raise ValueError("SMART_COMPLIANCE_MODEL_ENDPOINTS 必须是 JSON 数组")
    configured: list[str] = []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            raise ValueError("模型端点必须是 JSON 对象")
        model = endpoint.get("model")
        if model == "glm-5.3-flash":
            _update_endpoint(endpoint, timeout=240, thinking="enabled", reasoning_effort="low")
        elif model == "deepseek-v4-flash":
            _update_endpoint(endpoint, timeout=240, thinking="disabled", reasoning_effort=None)
        elif model == "kimi-k3":
            _update_endpoint(endpoint, timeout=300, thinking="none", reasoning_effort=None)
            endpoint["temperature"] = 1
        else:
            continue
        configured.append(str(endpoint.get("name", model)))
    if len(configured) != 3:
        raise ValueError(f"预期更新 3 个既有端点，实际找到 {len(configured)} 个")
    lines[selected_index] = prefix + json.dumps(endpoints, ensure_ascii=False, separators=(",", ":"))
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return configured


def _update_endpoint(
    endpoint: dict[str, object],
    *,
    timeout: int,
    thinking: str,
    reasoning_effort: str | None,
) -> None:
    endpoint.update(
        {
            "response_format": "json_object",
            "max_output_tokens": 4096,
            "timeout_seconds": timeout,
            "max_retries": 2,
            "thinking": thinking,
        }
    )
    if reasoning_effort is None:
        endpoint.pop("reasoning_effort", None)
    else:
        endpoint["reasoning_effort"] = reasoning_effort


def main() -> None:
    """更新项目根目录 `.env`，终端仅显示非敏感端点名称。"""
    root = Path(__file__).resolve().parents[1]
    configured = configure(root / ".env")
    click.echo(json.dumps({"configured_endpoints": configured}, ensure_ascii=False))


if __name__ == "__main__":
    main()
