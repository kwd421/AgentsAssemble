"""Compatibility exports for retained resident discovery."""
from agentsassemble.legacy.live_agent.discovery import (
    LegacyLiveAgentDiscoveryService,
    discovery_operation_details,
    live_agent_discovery_payload,
)

__all__ = [
    "LegacyLiveAgentDiscoveryService",
    "discovery_operation_details",
    "live_agent_discovery_payload",
]
