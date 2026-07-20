"""Compatibility exports for retained resident engagement HTTP routes."""

from agentsassemble.legacy.live_agent.http.engagement import (
    register_legacy_live_agent_engagement_route,
)

__all__ = ["register_legacy_live_agent_engagement_route"]
