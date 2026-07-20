"""Compatibility exports for retained resident read-only HTTP routes."""

from agentsassemble.legacy.live_agent.http.read import (
    LegacyLiveAgentReadDeps,
    register_legacy_live_agent_read_routes,
)

__all__ = ["LegacyLiveAgentReadDeps", "register_legacy_live_agent_read_routes"]
