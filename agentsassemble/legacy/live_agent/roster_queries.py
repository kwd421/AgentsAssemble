"""Safe roster and admission projections for retained resident agents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.legacy.meeting.records import (
    live_agent_admission_details,
    merge_live_progress_from_path,
    safe_meeting_dir,
)
from agentsassemble.legacy.live_agent.runtime.quota import LIVE_AGENT_QUOTA_FIELDS
from agentsassemble.legacy.live_agent.runtime.roster import filter_live_agent_roster, safe_live_agent_roster_payload
from agentsassemble.live_agents import read_live_agents
from agentsassemble.legacy.meeting.core.events import clean_lobby_text


@dataclass(frozen=True)
class LegacyLiveAgentRosterQueryService:
    output_root: Path

    def list(
        self,
        *,
        meeting_id: str = "",
        agent_ids: list[str] | None = None,
        statuses: list[str] | None = None,
        safe: bool = False,
    ) -> dict[str, object]:
        return live_agents_payload(
            self.output_root,
            meeting_id=meeting_id,
            agent_ids=agent_ids,
            statuses=statuses,
            safe=safe,
        )


def live_agents_payload(
    output_root: Path,
    *,
    meeting_id: str = "",
    agent_ids: list[str] | None = None,
    statuses: list[str] | None = None,
    safe: bool = False,
) -> dict[str, object]:
    payload = {
        "agents": filter_live_agent_roster(
            read_live_agents(output_root),
            meeting_id=meeting_id,
            agent_ids=agent_ids or [],
            statuses=statuses or [],
        )
    }
    if safe:
        return safe_live_agent_roster_payload(live_agent_roster_with_admission_evidence(output_root, payload))
    return {
        "agents": [
            live_agent_without_quota_fields(agent)
            for agent in payload["agents"]
            if isinstance(agent, dict)
        ]
    }


def live_agent_roster_with_admission_evidence(
    output_root: Path,
    payload: dict[str, object],
) -> dict[str, object]:
    agents = payload.get("agents") if isinstance(payload.get("agents"), list) else []
    return {
        "agents": [
            {
                **_live_agent_without_admission_evidence(agent),
                **live_agent_roster_admission_details(output_root, agent),
                "admission_evidence_source": "meeting_record",
            }
            for agent in agents
            if isinstance(agent, dict)
        ]
    }


def live_agent_register_admission_details(
    output_root: Path,
    agent: dict[str, object],
) -> dict[str, object]:
    return _live_agent_admission_details(output_root, agent)


def live_agent_roster_admission_details(
    output_root: Path,
    agent: dict[str, object],
) -> dict[str, object]:
    return _live_agent_admission_details(output_root, agent)


def live_agent_without_quota_fields(agent: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in agent.items() if key not in LIVE_AGENT_QUOTA_FIELDS}


def _live_agent_without_admission_evidence(agent: dict[str, object]) -> dict[str, object]:
    admission_fields = {
        "admission_status",
        "host_approved_binding",
        "binding_role_id",
        "binding_provider_id",
        "binding_provider_kind",
        "binding_permission_profile_id",
        "binding_join_mode",
        "binding_conflicts",
        "admission_evidence_source",
    }
    return {key: value for key, value in agent.items() if key not in admission_fields}


def _live_agent_admission_details(
    output_root: Path,
    agent: dict[str, object],
) -> dict[str, object]:
    agent_id = clean_lobby_text(agent.get("agent_id"), limit=64)
    meeting_id = clean_lobby_text(agent.get("meeting_id"), limit=128)
    if not meeting_id:
        return {"admission_status": "lobby_only", "host_approved_binding": False}
    try:
        meeting = _strict_meeting_record_for_admission(output_root, meeting_id)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"admission_status": "meeting_missing", "host_approved_binding": False}
    return live_agent_admission_details(meeting, agent, agent_id=agent_id)


def _strict_meeting_record_for_admission(output_root: Path, meeting_id: str) -> dict[str, object]:
    """Read only a host-authored meeting shape as binding evidence."""

    meeting_dir = safe_meeting_dir(output_root, meeting_id)
    meeting_path = meeting_dir / "meeting.json"
    live_path = meeting_dir / "live_state.json"
    if meeting_path.exists():
        meeting = json.loads(meeting_path.read_text(encoding="utf-8"))
        return merge_live_progress_from_path(meeting, live_path)
    if live_path.exists():
        record = json.loads(live_path.read_text(encoding="utf-8"))
        if isinstance(record, dict) and all(key in record for key in ("question", "topic", "roles")):
            return record
    raise ValueError("Meeting record is missing.")
