"""Aggregate read-only health projection for retained resident agents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.legacy.live_agent.health import (
    LIVE_AGENT_ADMISSION_HEALTH_STATUSES,
    diagnostic_agent_group_ids,
    is_diagnostic_agent,
    is_diagnostic_process_group,
    live_agent_process_health_reason,
    live_agent_process_monitor_summary,
    live_agent_process_status_summary,
    live_agent_status_summary,
    safe_health_identity,
)
from agentsassemble.legacy.live_agent.observation_health import live_agent_observation_health_summary
from agentsassemble.legacy.live_agent.process_projection import (
    agent_connection_evidence,
    safe_agent_connection_identity,
)
from agentsassemble.legacy_live_agent_roster_queries import live_agent_roster_with_admission_evidence
from agentsassemble.legacy_live_agent_session_run_health import (
    live_agent_session_run_health_summary,
    live_agent_session_run_monitor_health_summary,
)
from agentsassemble.live_agent_processes import LiveAgentProcessSupervisor
from agentsassemble.live_agent_sessions import live_agent_session_readiness_summary
from agentsassemble.live_agents import read_live_agents
from agentsassemble.live_meeting_memory import build_live_meeting_memory
from agentsassemble.meeting_events import read_live_events
from agentsassemble.live_agent_roster import safe_live_agent_roster_payload
from agentsassemble.application.session_run_monitor import PeriodicSessionRunMonitor


@dataclass(frozen=True)
class LegacyLiveAgentHealthQueryService:
    output_root: Path
    processes: LiveAgentProcessSupervisor
    session_run_monitor: PeriodicSessionRunMonitor | None = None

    def health(self) -> dict[str, object]:
        return live_agent_health_payload(
            self.output_root,
            self.processes,
            session_run_monitor=self.session_run_monitor,
        )


def live_agent_health_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    *,
    session_run_monitor: PeriodicSessionRunMonitor | None = None,
) -> dict[str, object]:
    agents = read_live_agents(output_root)
    groups = process_supervisor.snapshot_groups()
    diagnostic_group_ids = diagnostic_agent_group_ids(agents)
    agent_summary = live_agent_status_summary(agents)
    admission_summary = _live_agent_admission_health_summary(output_root, agents)
    process_summary = live_agent_process_status_summary(groups, diagnostic_group_ids=diagnostic_group_ids)
    connection_summary = _live_agent_connection_health_summary(
        groups,
        agents,
        diagnostic_group_ids=diagnostic_group_ids,
    )
    session_summary = _live_agent_session_health_summary(
        output_root,
        groups,
        diagnostic_group_ids=diagnostic_group_ids,
    )
    observation_summary = live_agent_observation_health_summary(
        output_root,
        groups,
        agents,
        session_summary,
        diagnostic_group_ids=diagnostic_group_ids,
    )
    sandbox_enforcement_summary = _live_agent_sandbox_enforcement_health_summary(agents)
    process_monitor_summary = live_agent_process_monitor_summary(process_supervisor)
    process_monitor_attention = (
        process_monitor_summary.get("attention")
        if isinstance(process_monitor_summary.get("attention"), list)
        else []
    )
    shared_memory_summary = _live_agent_shared_memory_health_summary(output_root, session_summary)
    shared_memory_attention = (
        shared_memory_summary.get("attention")
        if isinstance(shared_memory_summary.get("attention"), list)
        else []
    )
    session_run_summary = live_agent_session_run_health_summary(output_root, session_summary=session_summary)
    session_run_monitor_summary = live_agent_session_run_monitor_health_summary(session_run_monitor)
    session_run_monitor_attention = (
        session_run_monitor_summary.get("attention")
        if isinstance(session_run_monitor_summary.get("attention"), list)
        else []
    )
    sandbox_enforcement_attention = (
        sandbox_enforcement_summary.get("attention")
        if isinstance(sandbox_enforcement_summary.get("attention"), list)
        else []
    )
    status = (
        "degraded"
        if agent_summary["attention"]
        or process_summary["attention"]
        or process_monitor_attention
        or connection_summary["attention"]
        or session_summary["attention"]
        or observation_summary["attention"]
        or sandbox_enforcement_attention
        or shared_memory_attention
        or session_run_summary["attention"]
        or session_run_monitor_attention
        else "ok"
    )
    payload = {
        "status": status,
        "agents": agent_summary,
        "admission": admission_summary,
        "processes": process_summary,
        "connections": connection_summary,
        "sessions": session_summary,
        "observations": observation_summary,
        "sandbox_enforcement": sandbox_enforcement_summary,
        "shared_memory": shared_memory_summary,
        "session_runs": session_run_summary,
    }
    if process_monitor_summary:
        payload["process_monitor"] = process_monitor_summary
    if session_run_monitor_summary:
        payload["session_run_monitor"] = session_run_monitor_summary
    return payload


def _live_agent_admission_health_summary(
    output_root: Path,
    agents: list[dict[str, object]],
) -> dict[str, object]:
    visible_agents = [agent for agent in agents if not is_diagnostic_agent(agent)]
    safe_payload = safe_live_agent_roster_payload(
        live_agent_roster_with_admission_evidence(output_root, {"agents": visible_agents})
    )
    safe_agents = _as_dict_list(safe_payload.get("agents"))
    counts = {status: 0 for status in LIVE_AGENT_ADMISSION_HEALTH_STATUSES}
    host_approved = 0
    attention: list[str] = []
    for index, agent in enumerate(safe_agents, start=1):
        status = str(agent.get("admission_status") or "")
        if status not in counts:
            status = "unknown"
        counts[status] += 1
        if agent.get("host_approved_binding") is True:
            host_approved += 1
            continue
        meeting_id = safe_health_identity(agent.get("meeting_id")) or "lobby"
        agent_id = safe_health_identity(agent.get("agent_id")) or f"missing-agent-id-{index}"
        attention.append(f"{meeting_id}:{agent_id}:{status}")
    return {
        "total": len(safe_agents),
        "host_approved": host_approved,
        "unapproved": len(safe_agents) - host_approved,
        "counts": counts,
        "attention": attention,
    }


def _live_agent_sandbox_enforcement_health_summary(
    agents: list[dict[str, object]],
) -> dict[str, object]:
    safe_payload = safe_live_agent_roster_payload(
        {"agents": [agent for agent in agents if not is_diagnostic_agent(agent)]}
    )
    safe_agents = _as_dict_list(safe_payload.get("agents"))
    counts = {"advisory": 0, "codex_readonly": 0, "os_sandboxed": 0, "unknown": 0}
    attention = []
    for index, agent in enumerate(safe_agents, start=1):
        enforcement = str(agent.get("sandbox_enforcement") or "")
        if enforcement not in counts:
            enforcement = "unknown"
        counts[enforcement] += 1
        if enforcement == "unknown":
            agent_id = safe_health_identity(agent.get("agent_id")) or f"missing-agent-id-{index}"
            attention.append(agent_id)
    return {"counts": counts, "attention": attention}


def _live_agent_shared_memory_health_summary(
    output_root: Path,
    session_summary: dict[str, object],
) -> dict[str, object]:
    ready_sessions = 0
    items: list[dict[str, object]] = []
    latest_event_id = ""
    official_event_count = 0
    open_question_count = 0
    action_item_count = 0
    decision_count = 0
    attention: list[str] = []
    for session in _as_dict_list(session_summary.get("items")):
        if str(session.get("status") or "") != "ready":
            continue
        ready_sessions += 1
        meeting_id = safe_health_identity(session.get("meeting_id"))
        group_id = safe_health_identity(session.get("group_id"))
        if not meeting_id or not group_id:
            continue
        meeting_dir = output_root / "meetings" / meeting_id
        try:
            meeting = _read_live_agent_health_meeting(meeting_dir)
            memory = build_live_meeting_memory(read_live_events(meeting_dir, limit=None), meeting=meeting)
        except Exception:
            attention.append(f"{meeting_id}:{group_id}:memory_unavailable")
            continue
        item = _live_agent_shared_memory_health_item(memory, meeting_id=meeting_id, group_id=group_id)
        if not item:
            continue
        items.append(item)
        official_event_count += int(item["official_event_count"])
        open_question_count += int(item["open_question_count"])
        action_item_count += int(item["action_item_count"])
        decision_count += int(item["decision_count"])
        latest_event_id = str(item.get("last_official_event_id") or latest_event_id)
    return {
        "ready_sessions": ready_sessions,
        "with_memory": len(items),
        "official_event_count": official_event_count,
        "decision_count": decision_count,
        "open_question_count": open_question_count,
        "action_item_count": action_item_count,
        "last_official_event_id": latest_event_id,
        "attention": attention,
        "items": items,
    }


def _live_agent_shared_memory_health_item(
    memory: dict[str, object],
    *,
    meeting_id: str,
    group_id: str,
) -> dict[str, object]:
    official_event_count = _nonnegative_int(memory.get("official_event_count"), 0)
    if official_event_count <= 0:
        return {}
    return {
        "meeting_id": meeting_id,
        "group_id": group_id,
        "official_event_count": official_event_count,
        "official_message_count": _nonnegative_int(memory.get("official_message_count"), 0),
        "official_synthesis_count": _nonnegative_int(memory.get("official_synthesis_count"), 0),
        "decision_count": _nonnegative_int(memory.get("decision_count"), _memory_item_count(memory.get("decisions"))),
        "open_question_count": _nonnegative_int(
            memory.get("open_question_count"),
            _memory_item_count(memory.get("open_questions")),
        ),
        "action_item_count": _nonnegative_int(
            memory.get("action_item_count"),
            _memory_item_count(memory.get("action_items")),
        ),
        "last_official_event_id": safe_health_identity(memory.get("last_official_event_id")),
    }


def _read_live_agent_health_meeting(meeting_dir: Path) -> dict[str, object]:
    try:
        value = json.loads((meeting_dir / "meeting.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _live_agent_connection_health_summary(
    groups: list[dict[str, object]],
    agents: list[dict[str, object]],
    *,
    diagnostic_group_ids: set[str] | None = None,
) -> dict[str, object]:
    diagnostic_group_ids = diagnostic_group_ids or set()
    visible_agents = [agent for agent in agents if not is_diagnostic_agent(agent)]
    expected = 0
    connected = 0
    attention = []
    for group in groups:
        if str(group.get("status") or "") != "running":
            continue
        if is_diagnostic_process_group(group, diagnostic_group_ids):
            continue
        group_connection = agent_connection_evidence(group, visible_agents)
        expected += int(group_connection.get("expected") or 0)
        connected += int(group_connection.get("connected") or 0)
        group_id = safe_agent_connection_identity(group.get("group_id"))
        for item in _as_dict_list(group_connection.get("attention")):
            agent_id = safe_agent_connection_identity(item.get("agent_id"))
            status = str(item.get("status") or "unknown")
            attention.append(f"{group_id}:{agent_id}:{status}")
    return {"expected": expected, "connected": connected, "attention": attention}


def _live_agent_session_health_summary(
    output_root: Path,
    groups: list[dict[str, object]],
    *,
    diagnostic_group_ids: set[str] | None = None,
) -> dict[str, object]:
    diagnostic_group_ids = diagnostic_group_ids or set()
    visible_groups = [
        group
        for group in groups
        if not is_diagnostic_process_group(group, diagnostic_group_ids)
    ]
    summary = live_agent_session_readiness_summary(output_root, visible_groups)
    reasons_by_group = {
        str(group.get("group_id") or ""): live_agent_process_health_reason(group)
        for group in visible_groups
    }
    for item in _as_dict_list(summary.get("items")):
        reason = reasons_by_group.get(str(item.get("group_id") or ""))
        if reason:
            item["process_reason"] = reason
    return summary


def _as_dict_list(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _nonnegative_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (OverflowError, TypeError, ValueError):
        return default
    return max(0, parsed)


def _memory_item_count(value: object) -> int:
    return len(value) if isinstance(value, list) else 0
