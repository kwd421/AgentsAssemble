"""Compatibility exports for provider runtime construction."""

from agentsassemble.providers.runtime_factory import (
    ProviderRuntimeFactoryError,
    runtime_from_config,
)


__all__ = [
    "ProviderRuntimeFactoryError",
    "runtime_from_config",
]
