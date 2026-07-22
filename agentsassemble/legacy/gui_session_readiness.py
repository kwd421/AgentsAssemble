"""Readiness and restart policy for retained GUI Agent Session controls."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from agentsassemble.legacy.gui_payload import (
    as_dict_list,
    operation_result_status,
    payload_bool,
    payload_nonnegative_float,
    payload_nonnegative_int,
)
from agentsassemble.legacy.live_agent.diagnostics import session_process_groups_snapshot
from agentsassemble.legacy.live_agent.health import safe_health_identity
from agentsassemble.legacy.live_agent.observation_health import (
    latest_live_agent_turn_request_for_agent,
    latest_lobby_event,
    live_agent_live_observation_status,
    live_agent_lobby_observation_status,
    live_agent_observation_events,
)
from agentsassemble.legacy.live_agent.process_projection import parse_public_timestamp
from agentsassemble.legacy.live_agent.runtime.processes import LiveAgentProcessSupervisor
from agentsassemble.legacy.live_agent.state import read_live_agents
from agentsassemble.legacy.meeting.core.events import clean_lobby_text
from agentsassemble.live_agent_runner import load_group_configs


SESSION_ENSURE_REASON_RESIDENT_SESSION_ID_DRIFT = "resident_session_id_drift"
SESSION_ENSURE_REASON_STALE_LOBBY_OBSERVATION = "stale_lobby_observation"
SESSION_ENSURE_REASON_STALE_LIVE_OBSERVATION = "stale_live_observation"

ReadinessPayload = Callable[..., dict[str, object]]


def session_payload_with_group_owner(
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
) -> dict[str, object]:
    if str(payload.get("meeting_id") or "").strip():
        return payload
    group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        return payload
    group = find_session_process_group(
        session_process_groups_snapshot(process_supervisor),
        group_id,
    )
    owned_meeting_id = safe_process_group_meeting_id(
        group.get("meeting_id") if group else "",
    )
    if not owned_meeting_id:
        return payload
    payload["meeting_id"] = owned_meeting_id
    payload["_meeting_id_resolved_from_group"] = True
    return payload


def safe_process_group_meeting_id(value: object) -> str:
    meeting_id = clean_lobby_text(value, limit=128)
    if not meeting_id or meeting_id in {".", ".."}:
        return ""
    if "/" in meeting_id or "\\" in meeting_id or Path(meeting_id).name != meeting_id:
        return ""
    return meeting_id


def ready_session_requires_restart_for_resident_session_drift(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    current: dict[str, object] | None,
    *,
    default_server: str,
) -> bool:
    if not isinstance(current, dict) or operation_result_status(current.get("status")) != "ready":
        return False
    live_agent_config_path = str(
        payload.get("live_agent_config_path") or payload.get("live_agent_config") or "",
    ).strip()
    if not live_agent_config_path:
        return False
    group_id = str(current.get("group_id") or payload.get("group_id") or "").strip()
    if not group_id:
        return False
    group = find_session_process_group(
        session_process_groups_snapshot(process_supervisor),
        group_id,
    )
    if str(group.get("status") or "") not in {"running", "restarting"}:
        return False
    if not process_group_uses_requested_config(group, live_agent_config_path):
        return False
    meeting_id = str(
        current.get("meeting_id") or payload.get("meeting_id") or "",
    ).strip()
    requested_session_ids = resident_session_ids_by_agent(
        live_agent_config_path,
        server=str(payload.get("server") or default_server),
        meeting_id=meeting_id,
    )
    if not requested_session_ids:
        return False
    agents_by_id = {
        str(agent.get("agent_id") or ""): agent
        for agent in read_live_agents(output_root)
    }
    for agent_id, requested_session_id in requested_session_ids.items():
        current_agent = agents_by_id.get(agent_id)
        if not current_agent:
            continue
        if str(current_agent.get("meeting_id") or "").strip() != meeting_id:
            continue
        if str(current_agent.get("session_id") or "").strip() != requested_session_id:
            return True
    return False


def process_group_uses_requested_config(
    group: dict[str, object],
    live_agent_config_path: str,
) -> bool:
    persisted_config_path = str(group.get("config_path") or "").strip()
    if not persisted_config_path:
        return False
    return Path(persisted_config_path).resolve(strict=False) == Path(
        live_agent_config_path,
    ).resolve(strict=False)


def resident_session_ids_by_agent(
    live_agent_config_path: str,
    *,
    server: str,
    meeting_id: str,
) -> dict[str, str]:
    configs = load_group_configs(Path(live_agent_config_path), server_override=server)
    result: dict[str, str] = {}
    for config in configs:
        config_meeting_id = str(getattr(config, "meeting_id", "") or "").strip()
        if config_meeting_id and meeting_id and config_meeting_id != meeting_id:
            continue
        agent_id = str(getattr(config, "agent_id", "") or "").strip()
        session_id = str(getattr(config, "session_id", "") or "").strip()
        if agent_id and session_id:
            result[agent_id] = session_id
    return result


def ready_session_requires_restart_for_stale_observation_lag(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    current: dict[str, object] | None,
) -> bool:
    return stale_observation_restart_count(
        output_root,
        process_supervisor,
        payload,
        current,
    ) > 0


def stale_observation_restart_count(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    current: dict[str, object] | None,
) -> int:
    restart_count, _reason = stale_observation_restart_decision(
        output_root,
        process_supervisor,
        payload,
        current,
    )
    return restart_count


def stale_observation_restart_decision(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    current: dict[str, object] | None,
) -> tuple[int, str]:
    if not isinstance(current, dict) or operation_result_status(current.get("status")) != "ready":
        return 0, ""
    meeting_id = str(
        current.get("meeting_id") or payload.get("meeting_id") or "",
    ).strip()
    group_id = str(current.get("group_id") or payload.get("group_id") or "").strip()
    if not meeting_id or not group_id:
        return 0, ""
    group = find_session_process_group(
        session_process_groups_snapshot(process_supervisor),
        group_id,
    )
    if str(group.get("status") or "") != "running":
        return 0, ""
    stale_after_seconds = observation_restart_stale_after_seconds(group)
    if stale_after_seconds <= 0:
        return 0, ""
    agent_ids = [
        safe_health_identity(agent.get("agent_id"))
        for agent in as_dict_list(group.get("agents"))
        if safe_health_identity(agent.get("agent_id"))
    ]
    if not agent_ids:
        return 0, ""
    agents_by_id = {
        safe_health_identity(agent.get("agent_id")): agent
        for agent in read_live_agents(output_root)
        if safe_health_identity(agent.get("agent_id"))
    }
    restart_count = payload_nonnegative_int(group.get("restart_count"), 0) + 1
    if ready_session_has_stale_lobby_observation_lag(
        output_root,
        agent_ids,
        agents_by_id,
        stale_after_seconds=stale_after_seconds,
    ):
        return restart_count, SESSION_ENSURE_REASON_STALE_LOBBY_OBSERVATION
    if ready_session_has_stale_live_observation_lag(
        output_root,
        meeting_id,
        agent_ids,
        agents_by_id,
        stale_after_seconds=stale_after_seconds,
    ):
        return restart_count, SESSION_ENSURE_REASON_STALE_LIVE_OBSERVATION
    return 0, ""


def observation_restart_stale_after_seconds(group: dict[str, object]) -> float:
    if not payload_bool(group.get("auto_restart")):
        return 0.0
    max_restarts = payload_nonnegative_int(group.get("max_restarts"), 0)
    restart_count = payload_nonnegative_int(group.get("restart_count"), 0)
    if max_restarts <= 0 or restart_count >= max_restarts:
        return 0.0
    return payload_nonnegative_float(group.get("stale_restart_after_seconds"), 0.0)


def ready_session_has_stale_lobby_observation_lag(
    output_root: Path,
    agent_ids: list[str],
    agents_by_id: dict[str, dict[str, object]],
    *,
    stale_after_seconds: float,
) -> bool:
    event = latest_lobby_event(output_root)
    latest_event_id = safe_health_identity(event.get("id"))
    latest_actor_id = safe_health_identity(event.get("actor_id"))
    if not latest_event_id or not event_is_stale_for_observation_restart(
        event,
        stale_after_seconds,
    ):
        return False
    for agent_id in agent_ids:
        agent = agents_by_id.get(agent_id, {})
        status = live_agent_lobby_observation_status(
            latest_event_id,
            safe_health_identity(agent.get("last_observed_event_id")),
            latest_actor_id=latest_actor_id,
            agent_id=agent_id,
        )
        if status == "behind":
            return True
    return False


def ready_session_has_stale_live_observation_lag(
    output_root: Path,
    meeting_id: str,
    agent_ids: list[str],
    agents_by_id: dict[str, dict[str, object]],
    *,
    stale_after_seconds: float,
) -> bool:
    meeting_events = live_agent_observation_events(output_root, meeting_id, {})
    for agent_id in agent_ids:
        latest_request = latest_live_agent_turn_request_for_agent(
            meeting_events,
            agent_id,
        )
        if not latest_request or not event_is_stale_for_observation_restart(
            latest_request,
            stale_after_seconds,
        ):
            continue
        latest_request_id = safe_health_identity(latest_request.get("id"))
        agent = agents_by_id.get(agent_id, {})
        status = live_agent_live_observation_status(
            meeting_events,
            agent_id=agent_id,
            latest_request_id=latest_request_id,
            last_observed_live_event_id=safe_health_identity(
                agent.get("last_observed_live_event_id"),
            ),
        )
        if status == "behind":
            return True
    return False


def event_is_stale_for_observation_restart(
    event: dict[str, object],
    stale_after_seconds: float,
) -> bool:
    if stale_after_seconds <= 0:
        return False
    created_at = parse_public_timestamp(event.get("created_at"))
    if created_at is None:
        return False
    return (datetime.now(UTC) - created_at).total_seconds() >= stale_after_seconds


def optional_readiness_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    readiness_payload: ReadinessPayload,
) -> dict[str, object] | None:
    meeting_id = str(payload.get("meeting_id") or "").strip()
    group_id = str(payload.get("group_id") or "").strip()
    if not meeting_id or not group_id:
        return None
    try:
        return readiness_payload(
            output_root,
            process_supervisor,
            meeting_id=meeting_id,
            group_id=group_id,
        )
    except ValueError as error:
        if "was not found" in str(error):
            if payload.get("_meeting_id_resolved_from_group"):
                raise
            return None
        raise


def ensured_readiness_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    session: dict[str, object],
    *,
    readiness_payload: ReadinessPayload,
) -> dict[str, object]:
    meeting_id = str(
        session.get("meeting_id") or payload.get("meeting_id") or "",
    ).strip()
    group_id = str(session.get("group_id") or payload.get("group_id") or "").strip()
    if meeting_id and group_id:
        ensured = readiness_payload(
            output_root,
            process_supervisor,
            meeting_id=meeting_id,
            group_id=group_id,
        )
    else:
        ensured = dict(session)
    for key in ("reply_probe", "auto_rounds", "finalization"):
        value = session.get(key)
        if isinstance(value, dict):
            ensured[key] = value
    return ensured


def find_session_process_group(
    groups: list[dict[str, object]],
    group_id: str,
) -> dict[str, object]:
    for group in groups:
        if str(group.get("group_id") or "") == group_id:
            return group
    return {}
