"""Compatibility exports for retained resident configuration preflight."""
from agentsassemble.legacy.live_agent.preflight import (
    LegacyLiveAgentPreflightService,
    live_agent_preflight_payload,
)

__all__ = [
    "LegacyLiveAgentPreflightService",
    "live_agent_preflight_payload",
]
