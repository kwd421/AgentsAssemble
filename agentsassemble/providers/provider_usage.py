"""Owner-only provider usage registry and public error contract."""

from __future__ import annotations

from agentsassemble.providers.usage_contract import (
    ProviderUsageReader,
    ProviderUsageUnavailable,
)


class ProviderUsageRegistry:
    def __init__(self, readers: dict[str, ProviderUsageReader]) -> None:
        self._readers = dict(readers)

    def read(
        self,
        provider_id: str,
        *,
        model: str = "",
        refresh: bool = False,
    ) -> dict[str, object]:
        reader = self._readers.get(str(provider_id or "").strip().casefold())
        if reader is None:
            raise ProviderUsageUnavailable("provider_usage_not_supported")
        return reader.read(model=model, refresh=refresh)


def default_provider_usage_registry() -> ProviderUsageRegistry:
    from agentsassemble.providers.claude_usage import CLAUDE_USAGE
    from agentsassemble.providers.codex_usage import CODEX_USAGE
    from agentsassemble.providers.deepseek_usage import DEEPSEEK_USAGE
    from agentsassemble.providers.terminal_usage import (
        ANTIGRAVITY_USAGE,
        GROK_USAGE,
    )

    return ProviderUsageRegistry(
        {
            "claude": CLAUDE_USAGE,
            "codex": CODEX_USAGE,
            "deepseek": DEEPSEEK_USAGE,
            "antigravity": ANTIGRAVITY_USAGE,
            "grok": GROK_USAGE,
        }
    )


__all__ = [
    "ProviderUsageReader",
    "ProviderUsageRegistry",
    "ProviderUsageUnavailable",
    "default_provider_usage_registry",
]
