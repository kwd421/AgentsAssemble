"""Owner-only provider usage registry and public error contract."""

from __future__ import annotations

import threading
import time

from agentsassemble.providers.usage_contract import (
    ProviderUsageReader,
    ProviderUsageUnavailable,
)


class ProviderUsageRegistry:
    def __init__(
        self,
        readers: dict[str, ProviderUsageReader],
        *,
        failure_cooldown_seconds: float = 30.0,
    ) -> None:
        self._readers = dict(readers)
        self._provider_locks = {
            provider_id: threading.Lock()
            for provider_id in self._readers
        }
        self._failure_cooldown_seconds = max(
            1.0,
            float(failure_cooldown_seconds),
        )
        self._recent_failures: dict[str, tuple[float, str]] = {}

    def read(
        self,
        provider_id: str,
        *,
        model: str = "",
        refresh: bool = False,
    ) -> dict[str, object]:
        normalized_provider_id = str(provider_id or "").strip().casefold()
        reader = self._readers.get(normalized_provider_id)
        if reader is None:
            raise ProviderUsageUnavailable("provider_usage_not_supported")
        provider_lock = self._provider_locks[normalized_provider_id]
        with provider_lock:
            recent_failure = self._recent_failures.get(normalized_provider_id)
            if (
                not refresh
                and recent_failure
                and time.monotonic() - recent_failure[0]
                < self._failure_cooldown_seconds
            ):
                raise ProviderUsageUnavailable(recent_failure[1])
            try:
                payload = reader.read(model=model, refresh=refresh)
            except ProviderUsageUnavailable as error:
                self._recent_failures[normalized_provider_id] = (
                    time.monotonic(),
                    str(error),
                )
                raise
            self._recent_failures.pop(normalized_provider_id, None)
            return payload


def default_provider_usage_registry() -> ProviderUsageRegistry:
    from agentsassemble.providers.claude_usage import CLAUDE_USAGE
    from agentsassemble.providers.codex_usage import CODEX_USAGE
    from agentsassemble.providers.deepseek_usage import DEEPSEEK_USAGE
    from agentsassemble.providers.opencode_usage import OPENCODE_USAGE
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
            "opencode": OPENCODE_USAGE,
        }
    )


__all__ = [
    "ProviderUsageReader",
    "ProviderUsageRegistry",
    "ProviderUsageUnavailable",
    "default_provider_usage_registry",
]
