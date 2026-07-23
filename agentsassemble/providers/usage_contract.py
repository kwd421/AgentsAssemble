"""Provider usage interfaces shared without importing the usage registry."""

from __future__ import annotations

from typing import Protocol


class ProviderUsageUnavailable(RuntimeError):
    """A provider did not expose a usable account-usage snapshot."""


class ProviderUsageReader(Protocol):
    def read(
        self,
        *,
        model: str = "",
        refresh: bool = False,
    ) -> dict[str, object]: ...


__all__ = [
    "ProviderUsageReader",
    "ProviderUsageUnavailable",
]
