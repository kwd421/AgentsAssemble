"""Lifecycle commands for retained GUI Agent Session controls."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agentsassemble.legacy.gui_payload import (
    payload_bool,
    payload_nonnegative_float,
    payload_nonnegative_int,
)
from agentsassemble.legacy.gui_session_readiness import (
    SESSION_ENSURE_REASON_RESIDENT_SESSION_ID_DRIFT,
    ensured_readiness_payload,
    optional_readiness_payload,
    ready_session_requires_restart_for_resident_session_drift,
    session_payload_with_group_owner,
    stale_observation_restart_decision,
)
from agentsassemble.legacy.live_agent.diagnostics import live_agent_session_readiness_payload
from agentsassemble.legacy.live_agent.runtime.processes import LiveAgentProcessSupervisor
from agentsassemble.legacy.live_agent.runtime.sessions import (
    recover_live_agent_session,
    restart_live_agent_session,
    resume_live_agent_session,
    resume_live_agent_session_agent,
    session_ensure_action,
    start_live_agent_session,
    stop_live_agent_session,
    stop_live_agent_session_agent,
)


AttachAutoRounds = Callable[
    [Path, dict[str, object], dict[str, object]],
    dict[str, object],
]


def start_session_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
    attach_auto_rounds: AttachAutoRounds,
) -> dict[str, object]:
    council_config_path = str(
        payload.get("council_config_path") or payload.get("council_config") or "",
    ).strip()
    agent_config_path = str(
        payload.get("agent_config_path") or payload.get("agent_config") or "",
    ).strip()
    live_agent_config_path = str(
        payload.get("live_agent_config_path") or payload.get("live_agent_config") or "",
    ).strip()
    if not live_agent_config_path:
        raise ValueError("Live agent config path is required.")
    session = start_live_agent_session(
        output_root,
        process_supervisor,
        server=str(payload.get("server") or default_server),
        council_config_path=Path(council_config_path) if council_config_path else None,
        agent_config_path=Path(agent_config_path) if agent_config_path else None,
        live_agent_config_path=Path(live_agent_config_path),
        meeting_id=str(payload.get("meeting_id") or ""),
        group_id=str(payload.get("group_id") or ""),
        connect_timeout_seconds=payload_nonnegative_float(
            payload.get("connect_timeout_seconds"),
            5.0,
        ),
        auto_restart=payload_bool(payload.get("auto_restart")),
        max_restarts=payload_nonnegative_int(payload.get("max_restarts"), 0),
        restart_backoff_seconds=payload_nonnegative_float(
            payload.get("restart_backoff_seconds"),
            5.0,
        ),
        stale_restart_after_seconds=payload_nonnegative_float(
            payload.get("stale_restart_after_seconds"),
            0.0,
        ),
        diagnostic=payload_bool(payload.get("diagnostic")),
    )
    return attach_auto_rounds(output_root, session, payload)


def resume_session_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
    attach_auto_rounds: AttachAutoRounds,
) -> dict[str, object]:
    live_agent_config_path = str(
        payload.get("live_agent_config_path") or payload.get("live_agent_config") or "",
    ).strip()
    if not live_agent_config_path:
        raise ValueError("Live agent config path is required.")
    session = resume_live_agent_session(
        output_root,
        process_supervisor,
        server=str(payload.get("server") or default_server),
        live_agent_config_path=Path(live_agent_config_path),
        meeting_id=str(payload.get("meeting_id") or ""),
        group_id=str(payload.get("group_id") or ""),
        connect_timeout_seconds=payload_nonnegative_float(
            payload.get("connect_timeout_seconds"),
            5.0,
        ),
        auto_restart=payload_bool(payload.get("auto_restart")),
        max_restarts=payload_nonnegative_int(payload.get("max_restarts"), 0),
        restart_backoff_seconds=payload_nonnegative_float(
            payload.get("restart_backoff_seconds"),
            5.0,
        ),
        stale_restart_after_seconds=payload_nonnegative_float(
            payload.get("stale_restart_after_seconds"),
            0.0,
        ),
    )
    return attach_auto_rounds(output_root, session, payload)


def resume_session_agent_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
    attach_auto_rounds: AttachAutoRounds,
) -> dict[str, object]:
    live_agent_config_path = str(
        payload.get("live_agent_config_path") or payload.get("live_agent_config") or "",
    ).strip()
    session = resume_live_agent_session_agent(
        output_root,
        process_supervisor,
        server=str(payload.get("server") or default_server),
        live_agent_config_path=(
            Path(live_agent_config_path) if live_agent_config_path else None
        ),
        meeting_id=str(payload.get("meeting_id") or ""),
        group_id=str(payload.get("group_id") or ""),
        agent_id=str(payload.get("agent_id") or ""),
        connect_timeout_seconds=payload_nonnegative_float(
            payload.get("connect_timeout_seconds"),
            5.0,
        ),
        auto_restart=payload_bool(payload.get("auto_restart")),
        max_restarts=payload_nonnegative_int(payload.get("max_restarts"), 0),
        restart_backoff_seconds=payload_nonnegative_float(
            payload.get("restart_backoff_seconds"),
            5.0,
        ),
        stale_restart_after_seconds=payload_nonnegative_float(
            payload.get("stale_restart_after_seconds"),
            0.0,
        ),
    )
    return attach_auto_rounds(output_root, session, payload)


def ensure_session_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
    attach_auto_rounds: AttachAutoRounds,
) -> dict[str, object]:
    payload = session_payload_with_group_owner(process_supervisor, payload)
    current = optional_readiness_payload(
        output_root,
        process_supervisor,
        payload,
        readiness_payload=live_agent_session_readiness_payload,
    )
    action = session_ensure_action(current)
    stale_restart_count = 0
    ensure_reason = ""
    if action == "none" and ready_session_requires_restart_for_resident_session_drift(
        output_root,
        process_supervisor,
        payload,
        current,
        default_server=default_server,
    ):
        action = "restart"
        ensure_reason = SESSION_ENSURE_REASON_RESIDENT_SESSION_ID_DRIFT
    if action == "none":
        stale_restart_count, ensure_reason = stale_observation_restart_decision(
            output_root,
            process_supervisor,
            payload,
            current,
        )
        if stale_restart_count > 0:
            action = "restart"
    if action == "none":
        session = attach_auto_rounds(
            output_root,
            dict(current) if isinstance(current, dict) else {},
            payload,
        )
    elif action == "start":
        session = start_session_payload(
            output_root,
            process_supervisor,
            payload,
            default_server=default_server,
            attach_auto_rounds=attach_auto_rounds,
        )
    elif action == "restart":
        session = restart_session_payload(
            output_root,
            process_supervisor,
            payload,
            restart_count=stale_restart_count if stale_restart_count > 0 else None,
            attach_auto_rounds=attach_auto_rounds,
        )
    elif action == "recover":
        session = recover_session_payload(
            output_root,
            process_supervisor,
            payload,
            attach_auto_rounds=attach_auto_rounds,
        )
    else:
        session = resume_session_payload(
            output_root,
            process_supervisor,
            payload,
            default_server=default_server,
            attach_auto_rounds=attach_auto_rounds,
        )
    ensured = ensured_readiness_payload(
        output_root,
        process_supervisor,
        payload,
        session,
        readiness_payload=live_agent_session_readiness_payload,
    )
    ensured["action"] = action
    if ensure_reason:
        ensured["ensure_reason"] = ensure_reason
    return ensured


def restart_session_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    restart_count: int | None = None,
    attach_auto_rounds: AttachAutoRounds,
) -> dict[str, object]:
    group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        raise ValueError("Live agent group id is required.")
    session = restart_live_agent_session(
        output_root,
        process_supervisor,
        meeting_id=str(payload.get("meeting_id") or ""),
        group_id=group_id,
        connect_timeout_seconds=payload_nonnegative_float(
            payload.get("connect_timeout_seconds"),
            5.0,
        ),
        restart_count=restart_count,
    )
    return attach_auto_rounds(output_root, session, payload)


def recover_session_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    attach_auto_rounds: AttachAutoRounds,
) -> dict[str, object]:
    group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        raise ValueError("Live agent group id is required.")
    session = recover_live_agent_session(
        output_root,
        process_supervisor,
        meeting_id=str(payload.get("meeting_id") or ""),
        group_id=group_id,
        connect_timeout_seconds=payload_nonnegative_float(
            payload.get("connect_timeout_seconds"),
            5.0,
        ),
    )
    return attach_auto_rounds(output_root, session, payload)


def stop_session_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
) -> dict[str, object]:
    group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        raise ValueError("Live agent group id is required.")
    return stop_live_agent_session(
        output_root,
        process_supervisor,
        meeting_id=str(payload.get("meeting_id") or ""),
        group_id=group_id,
    )


def stop_session_agent_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
) -> dict[str, object]:
    return stop_live_agent_session_agent(
        output_root,
        process_supervisor,
        meeting_id=str(payload.get("meeting_id") or ""),
        group_id=str(payload.get("group_id") or ""),
        agent_id=str(payload.get("agent_id") or ""),
    )
