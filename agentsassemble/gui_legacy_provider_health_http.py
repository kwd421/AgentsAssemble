"""Compatibility exports for provider-health HTTP diagnostics."""

from agentsassemble.legacy.diagnostics.http.provider_health import (
    register_legacy_provider_health_route,
)

__all__ = ["register_legacy_provider_health_route"]
