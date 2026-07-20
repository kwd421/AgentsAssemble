"""Safe process-group projections shared by legacy reads and mutations."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agentsassemble.legacy.live_agent.health import safe_health_identity
from agentsassemble.legacy.live_agent.runtime.processes import LiveAgentProcessSupervisor
from agentsassemble.live_agents import read_live_agents
from agentsassemble.legacy.meeting.core.events import clean_lobby_text


def live_agent_processes_payload(
    process_supervisor: LiveAgentProcessSupervisor,
    *,
    output_root: Path | None = None,
) -> dict[str, object]:
    groups = process_supervisor.list_groups()
    if output_root is None:
        return {"groups": groups}
    return {"groups": groups_with_agent_connection_evidence(groups, read_live_agents(output_root))}


def process_payload_with_agent_connection_evidence(
    payload: dict[str, object],
    output_root: Path | None,
) -> dict[str, object]:
    if output_root is None:
        return payload
    agents = read_live_agents(output_root)
    response = dict(payload)
    group = response.get("group")
    if isinstance(group, dict):
        response["group"] = {**group, "agent_connection": agent_connection_evidence(group, agents)}
    groups = response.get("groups")
    if isinstance(groups, list):
        response["groups"] = groups_with_agent_connection_evidence(
            [group for group in groups if isinstance(group, dict)],
            agents,
        )
    return response


def groups_with_agent_connection_evidence(
    groups: list[dict[str, object]],
    agents: list[dict[str, object]],
) -> list[dict[str, object]]:
    return [{**group, "agent_connection": agent_connection_evidence(group, agents)} for group in groups]


def agent_connection_evidence(
    group: dict[str, object],
    agents: list[dict[str, object]],
) -> dict[str, object]:
    agents_by_id = {
        str(agent.get("agent_id") or ""): agent
        for agent in agents
        if str(agent.get("agent_id") or "")
    }
    group_meeting_id = safe_health_identity(group.get("meeting_id"))
    expected = 0
    connected = 0
    attention = []
    for manifest_agent in _as_dict_list(group.get("agents")):
        agent_id = str(manifest_agent.get("agent_id") or "").strip()
        if not agent_id:
            continue
        expected += 1
        agent = agents_by_id.get(agent_id)
        if agent is None:
            attention.append({"agent_id": agent_id, "status": "missing"})
            continue
        if group_meeting_id and str(agent.get("meeting_id") or "") != group_meeting_id:
            attention.append({"agent_id": agent_id, "status": "wrong_meeting"})
            continue
        if _agent_last_seen_before_group_start(agent, group):
            attention.append({"agent_id": agent_id, "status": "not_reconnected"})
            continue
        compatibility_attention = _manifest_agent_connection_attention(agent, manifest_agent)
        if compatibility_attention:
            attention.append({"agent_id": agent_id, "status": compatibility_attention})
            continue
        status = str(agent.get("status") or "offline")
        if status in {"online", "working"}:
            connected += 1
            continue
        if status not in {"error", "stale", "offline"}:
            status = "offline"
        attention.append({"agent_id": agent_id, "status": status})
    return {"expected": expected, "connected": connected, "attention": attention}


def safe_agent_connection_identity(value: object) -> str:
    return safe_health_identity(value) or "unknown"


def parse_public_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _manifest_agent_connection_attention(
    agent: dict[str, object],
    manifest_agent: dict[str, object],
) -> str:
    provider_kind = clean_lobby_text(manifest_agent.get("provider_kind"), limit=64)
    if provider_kind and clean_lobby_text(agent.get("provider_kind"), limit=64) != provider_kind:
        return "provider_kind_mismatch"
    connection_kind = clean_lobby_text(manifest_agent.get("connection_kind"), limit=64)
    if connection_kind and clean_lobby_text(agent.get("connection_kind"), limit=64) != connection_kind:
        return "connection_kind_mismatch"
    return ""


def _agent_last_seen_before_group_start(
    agent: dict[str, object],
    group: dict[str, object],
) -> bool:
    group_started_at = parse_public_timestamp(group.get("started_at"))
    agent_last_seen_at = parse_public_timestamp(agent.get("last_seen_at"))
    if group_started_at is None or agent_last_seen_at is None:
        return False
    return agent_last_seen_at < group_started_at


def _as_dict_list(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
