"""Compatibility exports for retained resident presence."""
from agentsassemble.legacy.live_agent.presence import (
    LegacyLiveAgentPresenceService,
    connect_live_agent_payload,
    live_agent_heartbeat_payload,
    live_agent_leave_payload,
)

__all__ = [
    "LegacyLiveAgentPresenceService",
    "connect_live_agent_payload",
    "live_agent_heartbeat_payload",
    "live_agent_leave_payload",
]
