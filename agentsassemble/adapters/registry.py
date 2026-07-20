"""Compatibility exports for the provider adapter registry."""

from agentsassemble.providers.adapters.registry import (
    REMOTE_BRIDGE_REQUESTER,
    AdapterFactory,
    ProviderCapabilities,
    ProviderRegistry,
    ResolvedAgentAdapter,
    default_provider_registry,
    register_http_provider_kinds,
    register_planned_provider_kinds,
    validate_binding,
)

__all__ = [
    "REMOTE_BRIDGE_REQUESTER",
    "AdapterFactory",
    "ProviderCapabilities",
    "ProviderRegistry",
    "ResolvedAgentAdapter",
    "default_provider_registry",
    "register_http_provider_kinds",
    "register_planned_provider_kinds",
    "validate_binding",
]
