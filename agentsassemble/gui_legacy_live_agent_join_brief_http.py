"""Compatibility exports for retained resident join-brief HTTP routes."""

from agentsassemble.legacy.live_agent.http.join_brief import (
    RequestServerUrl,
    register_legacy_live_agent_join_brief_route,
)

__all__ = ["RequestServerUrl", "register_legacy_live_agent_join_brief_route"]
