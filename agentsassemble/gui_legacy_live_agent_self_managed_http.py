"""Compatibility exports for retained self-managed resident HTTP routes."""

from agentsassemble.legacy.live_agent.http.self_managed import (
    register_legacy_self_managed_agent_routes,
)

__all__ = ["register_legacy_self_managed_agent_routes"]
