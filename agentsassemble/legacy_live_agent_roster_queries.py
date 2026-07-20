"""Compatibility exports for retained resident roster queries."""
from agentsassemble.legacy.live_agent.roster_queries import (
    LegacyLiveAgentRosterQueryService,
    live_agent_register_admission_details,
    live_agent_roster_admission_details,
    live_agent_roster_with_admission_evidence,
    live_agent_without_quota_fields,
    live_agents_payload,
)

__all__ = [
    "LegacyLiveAgentRosterQueryService",
    "live_agent_register_admission_details",
    "live_agent_roster_admission_details",
    "live_agent_roster_with_admission_evidence",
    "live_agent_without_quota_fields",
    "live_agents_payload",
]
