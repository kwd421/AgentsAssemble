"""Bounded operation-audit projections for retained resident presence."""
from __future__ import annotations

from pathlib import Path

from agentsassemble.legacy.live_agent.runtime.context import live_agent_context_contract_with_join_semantics
from agentsassemble.legacy.live_agent.roster_queries import live_agent_register_admission_details
from agentsassemble.legacy.meeting.core.events import clean_lobby_text


def registration_operation_details(
    output_root: Path,
    agent: dict[str, object],
    *,
    agent_id: str,
    previous_agent: dict[str, object],
) -> dict[str, object]:
    context_contract = live_agent_context_contract_with_join_semantics(
        agent.get("provider_kind"),
        agent.get("connection_kind"),
        agent.get("join_semantics"),
    )
    details = {
        "agent_id": clean_lobby_text(agent.get("agent_id") or agent_id, limit=64),
        "meeting_id": clean_lobby_text(agent.get("meeting_id"), limit=128),
        "provider_kind": clean_lobby_text(agent.get("provider_kind"), limit=64),
        "connection_kind": clean_lobby_text(agent.get("connection_kind"), limit=64),
        "join_semantics": context_contract["join_semantics"],
        "context_durability": context_contract["context_durability"],
        "sandbox_enforcement": context_contract["sandbox_enforcement"],
        "engagement_mode": clean_lobby_text(agent.get("engagement_mode"), limit=64),
        "previous_status": clean_lobby_text(previous_agent.get("status"), limit=32),
        "registered_status": clean_lobby_text(agent.get("status"), limit=32),
    }
    details.update(live_agent_register_admission_details(output_root, agent))
    return details


def leave_operation_details(
    agent: dict[str, object],
    *,
    agent_id: str,
    previous_agent: dict[str, object],
) -> dict[str, object]:
    return {
        "agent_id": clean_lobby_text(agent.get("agent_id") or agent_id, limit=64),
        "meeting_id": clean_lobby_text(agent.get("meeting_id"), limit=128),
        "previous_status": clean_lobby_text(previous_agent.get("status"), limit=32),
        "last_observed_event_id": clean_lobby_text(agent.get("last_observed_event_id"), limit=128),
        "last_observed_live_event_id": clean_lobby_text(
            agent.get("last_observed_live_event_id"),
            limit=128,
        ),
    }
