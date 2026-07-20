"""Compatibility exports for retained resident discovery HTTP routes."""

from agentsassemble.legacy.live_agent.http.discovery import (
    LegacyLiveAgentDiscoveryHttpDeps,
    ReadOperationPayload,
    RecordOperation,
    RequestServerUrl,
    register_legacy_live_agent_discovery_route,
)

__all__ = [
    "LegacyLiveAgentDiscoveryHttpDeps",
    "ReadOperationPayload",
    "RecordOperation",
    "RequestServerUrl",
    "register_legacy_live_agent_discovery_route",
]
