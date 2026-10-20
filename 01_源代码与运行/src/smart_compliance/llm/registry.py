"""模型端点注册表。"""

from smart_compliance.config import Settings
from smart_compliance.domain.exceptions import ModelConfigurationError
from smart_compliance.llm.openai_compatible import OpenAiCompatibleProvider


class ProviderRegistry:
    """按配置名称提供模型 provider。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化注册表。"""
        self._settings = settings or Settings()
        self._providers = {
            endpoint.name: OpenAiCompatibleProvider(endpoint, self._settings) for endpoint in self._settings.endpoints
        }

    def names(self) -> list[str]:
        """返回已配置端点名称。"""
        return sorted(self._providers)

    def get(self, name: str) -> OpenAiCompatibleProvider:
        """按名称获取 provider。

        Raises:
            ModelConfigurationError: 端点不存在。
        """
        try:
            return self._providers[name]
        except KeyError as error:
            available = ", ".join(self.names()) or "无"
            raise ModelConfigurationError(f"模型端点未配置: {name}；当前可用: {available}") from error
