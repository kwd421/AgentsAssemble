from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from agentsassemble.legacy.meeting_admission import MEETING_UNSAFE_PERMISSIONS
from agentsassemble.legacy.live_agent.runtime.finalization import _pending_turn_requests
from agentsassemble.legacy.meeting.core.events import read_live_events


STALE_RUNNING_SECONDS = 300
SAFE_PERMISSION_FIELDS = ("meeting_read", "lobby_chat", "official_turn", "web_search", "tool_use")
ACTIVE_AGENT_STATUSES = frozenset(("online", "working", "idle", "ready"))


def project_meeting_lifecycle(
    meeting_dir: Path,
    *,
    now: float | None = None,
    live_agents: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    record, has_final_record, status_source = _load_projection_record(meeting_dir)
    if status_source == "missing":
        return _projection(
            state="archived",
            status_source="missing_state",
            meeting=record,
            events=[],
            pending_turns=[],
            live_agents=live_agents,
            attention=[],
        )
    if status_source == "malformed":
        return _projection(
            state="unknown",
            status_source="malformed_record",
            meeting=record,
            events=[],
            pending_turns=[],
            live_agents=live_agents,
            attention=["malformed"],
        )

    meeting = infer_live_status(record, meeting_dir, has_final_record=has_final_record, now=now)
    source = "final_record" if has_final_record else status_source
    if meeting.get("live_status") == "stalled":
        source = "stale_running_inference"
    events = read_live_events(meeting_dir)
    pending_turns = _pending_turn_requests(events)
    state = _lifecycle_state(
        meeting=meeting,
        has_final_record=has_final_record,
        events=events,
        pending_turns=pending_turns,
        live_agents=live_agents,
    )
    attention = _attention_for_state(state, pending_turns=pending_turns)
    return _projection(
        state=state,
        status_source=source,
        meeting=meeting,
        events=events,
        pending_turns=pending_turns,
        live_agents=live_agents,
        attention=attention,
    )


def infer_live_status(
    meeting: dict[str, object],
    meeting_dir: Path,
    *,
    has_final_record: bool,
    now: float | None = None,
) -> dict[str, object]:
    if has_final_record or meeting.get("live_status") != "running":
        return meeting
    latest_mtime = latest_live_mtime(meeting_dir)
    if latest_mtime is None:
        return meeting
    if (now if now is not None else time.time()) - latest_mtime < STALE_RUNNING_SECONDS:
        return meeting
    inferred = dict(meeting)
    inferred["live_status"] = "stalled"
    inferred["stalled_reason"] = "No live meeting update has been observed recently."
    inferred["last_live_update_mtime"] = latest_mtime
    return inferred


def latest_live_mtime(meeting_dir: Path) -> float | None:
    mtimes = [
        path.stat().st_mtime
        for path in (meeting_dir / "live_state.json", meeting_dir / "live_events.jsonl")
        if path.exists()
    ]
    return max(mtimes) if mtimes else None


def _load_projection_record(meeting_dir: Path) -> tuple[dict[str, object], bool, str]:
    meeting_path = meeting_dir / "meeting.json"
    live_path = meeting_dir / "live_state.json"
    if meeting_path.exists():
        try:
            return json.loads(meeting_path.read_text(encoding="utf-8")), True, "final_record"
        except json.JSONDecodeError:
            if not live_path.exists():
                return {}, False, "malformed"
    if live_path.exists():
        try:
            return json.loads(live_path.read_text(encoding="utf-8")), False, "live_state"
        except json.JSONDecodeError:
            return {}, False, "malformed"
    return {}, False, "missing"


def _lifecycle_state(
    *,
    meeting: dict[str, object],
    has_final_record: bool,
    events: list[dict[str, object]],
    pending_turns: list[dict[str, object]],
    live_agents: list[dict[str, object]] | None,
) -> str:
    if has_final_record and meeting.get("live_status") == "complete":
        return "finalized"
    if meeting.get("live_status") == "stalled":
        return "stopped"
    if pending_turns:
        return "blocked_by_pending_turns"
    if _official_message_count(events):
        return "running_official_turns"
    bindings = _as_list_of_dicts(meeting.get("agent_bindings"))
    if bindings and not _has_active_bound_agent(bindings, live_agents or []):
        return "waiting_for_agents"
    return "preparing"


def _projection(
    *,
    state: str,
    status_source: str,
    meeting: dict[str, object],
    events: list[dict[str, object]],
    pending_turns: list[dict[str, object]],
    live_agents: list[dict[str, object]] | None,
    attention: list[str],
) -> dict[str, object]:
    roles = _as_list_of_dicts(meeting.get("roles"))
    bindings = _as_list_of_dicts(meeting.get("agent_bindings"))
    return {
        "state": state,
        "status_source": status_source,
        "counts": {
            "roles": len(roles),
            "bindings": len(bindings),
            "live_agents": len(live_agents or []),
            "pending_turns": len(pending_turns),
            "official_messages": _official_message_count(events),
        },
        "role_hints": _role_hints(meeting=meeting, roles=roles, bindings=bindings, live_agents=live_agents or []),
        "attention": attention,
    }


def _role_hints(
    *,
    meeting: dict[str, object],
    roles: list[dict[str, object]],
    bindings: list[dict[str, object]],
    live_agents: list[dict[str, object]],
) -> list[dict[str, object]]:
    permissions = _permission_profiles(meeting.get("permission_profiles"))
    bindings_by_role = {
        str(binding.get("role_id") or ""): binding
        for binding in bindings
        if str(binding.get("role_id") or "").strip()
    }
    live_agents_by_id = {
        str(agent.get("agent_id") or ""): agent
        for agent in live_agents
        if str(agent.get("agent_id") or "").strip()
    }
    hints = []
    for role in roles:
        role_id = str(role.get("id") or role.get("role_id") or "").strip()
        if not role_id:
            continue
        binding = bindings_by_role.get(role_id)
        profile = _profile_for_binding(binding, permissions)
        hints.append(
            {
                "role_id": role_id,
                "display_name": str(role.get("display_name") or role.get("name") or role_id),
                "admission_status": _admission_status(binding, live_agents_by_id),
                "permissions": _safe_permissions(profile),
                "unsafe_permission_violations": _unsafe_permission_count(profile),
            }
        )
    return hints


def _permission_profiles(raw: object) -> dict[str, dict[str, object]]:
    if isinstance(raw, dict):
        profiles = raw.values() if all(isinstance(value, dict) for value in raw.values()) else [raw]
    elif isinstance(raw, list):
        profiles = raw
    else:
        profiles = []
    result: dict[str, dict[str, object]] = {}
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        profile_id = str(profile.get("id") or "").strip()
        if profile_id:
            result[profile_id] = profile
    return result


def _profile_for_binding(
    binding: dict[str, object] | None,
    permissions: dict[str, dict[str, object]],
) -> dict[str, object]:
    if not binding:
        return {}
    profile_id = str(binding.get("permission_profile_id") or "").strip()
    return permissions.get(profile_id, {})


def _safe_permissions(profile: dict[str, object]) -> dict[str, bool]:
    defaults = {
        "meeting_read": True,
        "lobby_chat": True,
        "official_turn": True,
        "web_search": False,
        "tool_use": False,
    }
    return {
        field: _payload_bool(profile.get(field), default=defaults[field])
        for field in SAFE_PERMISSION_FIELDS
    }


def _unsafe_permission_count(profile: dict[str, object]) -> int:
    return sum(1 for field in MEETING_UNSAFE_PERMISSIONS if _payload_bool(profile.get(field), default=False))


def _admission_status(
    binding: dict[str, object] | None,
    live_agents_by_id: dict[str, dict[str, object]],
) -> str:
    if not binding:
        return "missing_binding"
    agent_id = str(binding.get("agent_id") or "").strip()
    live_agent = live_agents_by_id.get(agent_id)
    if not live_agent:
        return "waiting_for_agent"
    admission = str(live_agent.get("admission_status") or "").strip()
    if admission:
        return admission
    if live_agent.get("host_approved_binding") is True:
        return "bound_to_meeting"
    status = str(live_agent.get("status") or "").strip()
    return "present_unapproved" if status in ACTIVE_AGENT_STATUSES else "waiting_for_agent"


def _has_active_bound_agent(bindings: list[dict[str, object]], live_agents: list[dict[str, object]]) -> bool:
    bound_agent_ids = {str(binding.get("agent_id") or "").strip() for binding in bindings}
    bound_agent_ids.discard("")
    for agent in live_agents:
        agent_id = str(agent.get("agent_id") or "").strip()
        status = str(agent.get("status") or "").strip()
        admission = str(agent.get("admission_status") or "").strip()
        if agent_id in bound_agent_ids and status in ACTIVE_AGENT_STATUSES:
            return agent.get("host_approved_binding") is True or admission == "bound_to_meeting"
    return False


def _official_message_count(events: list[dict[str, object]]) -> int:
    return sum(
        1
        for event in events
        if event.get("official_record") is True and str(event.get("kind") or "") == "message"
    )


def _attention_for_state(state: str, *, pending_turns: list[dict[str, object]]) -> list[str]:
    if state == "blocked_by_pending_turns":
        return ["pending_official_turns"]
    if state == "stopped":
        return ["stalled_running_state"]
    return []


def _as_list_of_dicts(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _payload_bool(raw: object, *, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return default
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw)
