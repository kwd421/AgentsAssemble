from __future__ import annotations

import math

from agentsassemble.legacy.meeting.core.events import clean_lobby_text


SESSION_ENSURE_REASONS = {
    "resident_session_id_drift",
    "stale_lobby_observation",
    "stale_live_observation",
}


def session_start_operation_details(session: dict[str, object]) -> dict[str, object]:
    connection = _mapping(session.get("connection"))
    process = _mapping(session.get("process"))
    offline = _mapping(session.get("offline"))
    ownership = _mapping(session.get("ownership"))
    details = {
        "result_status": _result_status(session.get("status")),
        "meeting_id": clean_lobby_text(session.get("meeting_id"), limit=128),
        "group_id": clean_lobby_text(session.get("group_id"), limit=128),
        "expected_agent_count": _nonnegative_int(connection.get("expected"), 0),
        "connected_agent_count": _nonnegative_int(connection.get("connected"), 0),
        "agent_ids": _safe_strings(connection.get("agent_ids"), limit=64),
        "connected_agent_ids": _safe_strings(connection.get("connected_agent_ids"), limit=64),
        "attention": _safe_strings(connection.get("attention"), limit=128),
        "process_status": clean_lobby_text(process.get("status"), limit=64),
        "process_agent_ids": _safe_strings(process.get("agent_ids"), limit=64),
        "process_attention": _safe_strings(process.get("attention"), limit=128),
        "ownership_attention": _safe_strings(ownership.get("attention"), limit=128),
    }
    ensure_action = clean_lobby_text(session.get("action"), limit=64)
    if ensure_action:
        details["ensure_action"] = ensure_action
    ensure_reason = _safe_ensure_reason(session.get("ensure_reason"))
    if ensure_reason:
        details["ensure_reason"] = ensure_reason
    if offline:
        details.update(
            {
                "offline_agent_count": _nonnegative_int(offline.get("offline"), 0),
                "offline_agent_ids": _safe_strings(offline.get("offline_agent_ids"), limit=64),
                "offline_attention": _safe_strings(offline.get("attention"), limit=128),
            }
        )
    reply_probe = _optional_mapping(session.get("reply_probe"))
    if reply_probe is not None:
        details.update(_reply_probe_details(reply_probe))
    auto_rounds = _optional_mapping(session.get("auto_rounds"))
    if auto_rounds is not None:
        details.update(_auto_rounds_details(auto_rounds, str(session.get("meeting_id") or "")))
    finalization = _optional_mapping(session.get("finalization"))
    if finalization is not None:
        details.update(_finalization_details(finalization, str(session.get("meeting_id") or "")))
    return details


def session_check_operation_details(session: dict[str, object]) -> dict[str, object]:
    return session_start_operation_details(session)


def session_stop_operation_details(session: dict[str, object]) -> dict[str, object]:
    offline = _mapping(session.get("offline"))
    process = _mapping(session.get("process"))
    session_runs = session.get("session_runs") if isinstance(session.get("session_runs"), list) else []
    stopped_run_ids = [
        clean_lobby_text(run.get("run_id"), limit=64)
        for run in session_runs
        if isinstance(run, dict)
        and run.get("status") == "stopped"
        and clean_lobby_text(run.get("run_id"), limit=64)
    ]
    return {
        "result_status": _result_status(session.get("status")),
        "meeting_id": clean_lobby_text(session.get("meeting_id"), limit=128),
        "group_id": clean_lobby_text(session.get("group_id"), limit=128),
        "expected_agent_count": _nonnegative_int(offline.get("expected"), 0),
        "offline_agent_count": _nonnegative_int(offline.get("offline"), 0),
        "agent_ids": _safe_strings(offline.get("agent_ids"), limit=64),
        "offline_agent_ids": _safe_strings(offline.get("offline_agent_ids"), limit=64),
        "attention": _safe_strings(offline.get("attention"), limit=128),
        "process_status": clean_lobby_text(process.get("status"), limit=64),
        "process_agent_ids": _safe_strings(process.get("agent_ids"), limit=64),
        "process_attention": _safe_strings(process.get("attention"), limit=128),
        "session_run_stopped_count": len(stopped_run_ids),
        "session_run_ids": stopped_run_ids[:10],
    }


def _reply_probe_details(reply_probe: dict[str, object]) -> dict[str, object]:
    return {
        "reply_probe_status": _result_status(reply_probe.get("status")),
        "reply_probe_reason": clean_lobby_text(reply_probe.get("reason"), limit=128),
        "reply_probe_agent_ids": _safe_strings(reply_probe.get("agent_ids"), limit=64),
        "reply_probe_statuses": _probe_statuses(reply_probe.get("probes")),
        "reply_probe_count": _nonnegative_int(reply_probe.get("probe_count"), 0),
        "reply_probe_ok_count": _nonnegative_int(reply_probe.get("ok_count"), 0),
        "reply_probe_timeout_count": _nonnegative_int(reply_probe.get("timeout_count"), 0),
        "reply_probe_failed_count": _nonnegative_int(reply_probe.get("failed_count"), 0),
        "reply_probe_skipped_count": _nonnegative_int(reply_probe.get("skipped_count"), 0),
        "reply_probe_timeout_seconds": _nonnegative_float(reply_probe.get("timeout_seconds"), 0.0),
    }


def _auto_rounds_details(auto_rounds: dict[str, object], meeting_id: str) -> dict[str, object]:
    results = auto_rounds.get("results") if isinstance(auto_rounds.get("results"), list) else []
    round_ids: list[str] = []
    statuses: list[str] = []
    role_ids: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        round_id = clean_lobby_text(item.get("round_id"), limit=128)
        status = clean_lobby_text(item.get("status"), limit=32)
        if round_id:
            round_ids.append(round_id)
        if status:
            statuses.append(status)
        role_ids.extend(_safe_strings(item.get("role_ids"), limit=128))
    return {
        "auto_rounds_status": _result_status(auto_rounds.get("status")),
        "auto_rounds_reason": clean_lobby_text(auto_rounds.get("reason"), limit=128),
        "auto_rounds_meeting_id": meeting_id,
        "auto_rounds_round_count": _nonnegative_int(auto_rounds.get("round_count"), 0),
        "auto_rounds_answered_round_count": _nonnegative_int(auto_rounds.get("answered_round_count"), 0),
        "auto_rounds_completed_round_count": _nonnegative_int(auto_rounds.get("completed_round_count"), 0),
        "auto_rounds_timeout_round_count": _nonnegative_int(auto_rounds.get("timeout_round_count"), 0),
        "auto_rounds_skipped_round_count": _nonnegative_int(auto_rounds.get("skipped_round_count"), 0),
        "auto_rounds_stopped_round_count": _nonnegative_int(auto_rounds.get("stopped_round_count"), 0),
        "auto_rounds_stopped": auto_rounds.get("stopped") is True,
        "auto_rounds_round_ids": round_ids,
        "auto_rounds_statuses": statuses,
        "auto_rounds_role_ids": role_ids,
        "auto_rounds_timeout_seconds": _nonnegative_float(auto_rounds.get("timeout_seconds"), 0.0),
        "auto_rounds_max_rounds": _nonnegative_int(auto_rounds.get("max_rounds"), 0),
    }


def _finalization_details(finalization: dict[str, object], meeting_id: str) -> dict[str, object]:
    details = {
        "finalization_status": _result_status(finalization.get("status")),
        "finalization_reason": clean_lobby_text(finalization.get("reason"), limit=256),
        "finalization_meeting_id": clean_lobby_text(finalization.get("meeting_id") or meeting_id, limit=128),
        "finalization_official_event_count": _nonnegative_int(finalization.get("official_event_count"), 0),
        "finalization_artifact_event_id": clean_lobby_text(finalization.get("artifact_event_id"), limit=128),
    }
    memory = _mapping(finalization.get("shared_memory"))
    if memory:
        details.update(
            {
                "shared_memory_official_event_count": _nonnegative_int(memory.get("official_event_count"), 0),
                "shared_memory_last_event_id": clean_lobby_text(memory.get("last_official_event_id"), limit=128),
                "shared_memory_decision_count": _nonnegative_int(
                    memory.get("decision_count"), _list_count(memory.get("decisions"))
                ),
                "shared_memory_open_question_count": _nonnegative_int(
                    memory.get("open_question_count"), _list_count(memory.get("open_questions"))
                ),
                "shared_memory_action_item_count": _nonnegative_int(
                    memory.get("action_item_count"), _list_count(memory.get("action_items"))
                ),
            }
        )
    return details


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _optional_mapping(value: object) -> dict[str, object] | None:
    return value if isinstance(value, dict) else None


def _result_status(value: object) -> str:
    return str(value or "unknown").strip() or "unknown"


def _safe_ensure_reason(value: object) -> str:
    reason = clean_lobby_text(value, limit=64)
    return reason if reason in SESSION_ENSURE_REASONS else ""


def _safe_strings(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if isinstance(item, str) and (text := clean_lobby_text(item, limit=limit))]


def _probe_statuses(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    statuses: list[str] = []
    for probe in value:
        if not isinstance(probe, dict):
            continue
        agent_id = str(probe.get("agent_id") or "").strip()
        status = str(probe.get("status") or "unknown").strip() or "unknown"
        if agent_id:
            statuses.append(f"{agent_id}:{status}")
    return statuses


def _nonnegative_int(value: object, default: int) -> int:
    try:
        return max(0, int(value))
    except (OverflowError, TypeError, ValueError):
        return default


def _nonnegative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, parsed) if math.isfinite(parsed) else default


def _list_count(value: object) -> int:
    return len(value) if isinstance(value, list) else 0
