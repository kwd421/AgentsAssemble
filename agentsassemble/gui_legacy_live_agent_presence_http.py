"""Compatibility exports for retained resident presence HTTP routes."""

from agentsassemble.legacy.live_agent.http.presence import (
    register_legacy_live_agent_presence_routes,
)

__all__ = ["register_legacy_live_agent_presence_routes"]
