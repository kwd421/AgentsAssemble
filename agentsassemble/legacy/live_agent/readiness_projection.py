"""Safe response and operation projections for resident readiness checks."""

from __future__ import annotations

import math

from agentsassemble.diagnostics.report_projection import looks_sensitive_operator_diagnostic_text
from agentsassemble.legacy.meeting.core.events import clean_lobby_text


OFFICIAL_ROUND_SMOKE_ERROR = "official round smoke could not be run"
SESSION_SMOKE_ERROR = "session smoke could not be run"


def safe_readiness_smoke_result(smoke: dict[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {
        "status": str(smoke.get("status") or "unknown"),
        "group_id": str(smoke.get("group_id") or ""),
    }
    agent_ids = payload_probe_ids(smoke.get("agent_ids"))
    if agent_ids:
        safe["agent_ids"] = agent_ids
    replies = smoke.get("replies") if isinstance(smoke.get("replies"), list) else []
    safe["reply_count"] = len(replies)
    error = str(smoke.get("error") or "").strip()[:240]
    if error:
        safe["error"] = error
    return safe


def safe_readiness_official_round_smoke_result(smoke: dict[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {
        "status": str(smoke.get("status") or "unknown"),
        "group_id": clean_lobby_text(smoke.get("group_id"), limit=128),
        "meeting_id": clean_lobby_text(smoke.get("meeting_id"), limit=128),
        "round_id": clean_lobby_text(smoke.get("round_id"), limit=128),
        "agent_ids": _safe_strings(smoke.get("agent_ids"), limit=64),
        "role_ids": _safe_strings(smoke.get("role_ids"), limit=128),
        "turn_count": _nonnegative_int(smoke.get("turn_count"), 0),
        "answered_count": _nonnegative_int(smoke.get("answered_count"), 0),
        "timeout_count": _nonnegative_int(smoke.get("timeout_count"), 0),
        "skipped_count": _nonnegative_int(smoke.get("skipped_count"), 0),
        "stopped": smoke.get("stopped") is True,
        "timeout_seconds": _nonnegative_float(smoke.get("timeout_seconds"), 0.0),
        "statuses": _safe_strings(smoke.get("statuses"), limit=32),
    }
    if str(smoke.get("error") or "").strip():
        safe["error"] = OFFICIAL_ROUND_SMOKE_ERROR
    reason = str(smoke.get("reason") or "").strip()[:128]
    if reason:
        safe["reason"] = reason
    return safe


def safe_readiness_session_smoke_result(smoke: dict[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {
        "status": str(smoke.get("status") or "unknown"),
        "meeting_id": clean_lobby_text(smoke.get("meeting_id"), limit=128),
        "group_id": clean_lobby_text(smoke.get("group_id"), limit=128),
        "agent_ids": _safe_strings(smoke.get("agent_ids"), limit=64),
        "terminal_session_supported": smoke.get("terminal_session_supported") is True,
        "terminal_session_included": smoke.get("terminal_session_included") is True,
        "terminal_session_status": _result_status(smoke.get("terminal_session_status")),
        "terminal_session_reason": clean_lobby_text(smoke.get("terminal_session_reason"), limit=128),
        "rounds_status": _result_status(smoke.get("rounds_status")),
        "answered_round_count": _nonnegative_int(smoke.get("answered_round_count"), 0),
        "finalization_status": _result_status(smoke.get("finalization_status")),
        "finalization_official_event_count": _nonnegative_int(smoke.get("finalization_official_event_count"), 0),
        "return_packet_event_count": _nonnegative_int(smoke.get("return_packet_event_count"), 0),
        "artifact_status": _result_status(smoke.get("artifact_status")),
        "artifact_paths": _safe_strings(smoke.get("artifact_paths"), limit=128),
        "lobby_probe_count": _nonnegative_int(smoke.get("lobby_probe_count"), 1),
        "expected_reply_count": _nonnegative_int(smoke.get("expected_reply_count"), 0),
        "self_service_official_reply_count": _nonnegative_int(
            smoke.get("self_service_official_reply_count"), 0
        ),
        "self_service_lobby_reply_count": _nonnegative_int(smoke.get("self_service_lobby_reply_count"), 0),
        "self_service_post_restart_reply_count": _nonnegative_int(
            smoke.get("self_service_post_restart_reply_count"), 0
        ),
        "self_service_post_recover_reply_count": _nonnegative_int(
            smoke.get("self_service_post_recover_reply_count"), 0
        ),
        "self_service_soak_reply_count": _nonnegative_int(smoke.get("self_service_soak_reply_count"), 0),
        "reply_count": _nonnegative_int(smoke.get("reply_count"), 0),
        "post_restart_reply_count": _nonnegative_int(smoke.get("post_restart_reply_count"), 0),
        "post_recover_reply_count": _nonnegative_int(smoke.get("post_recover_reply_count"), 0),
        "soak_cycle_count": _nonnegative_int(smoke.get("soak_cycle_count"), 0),
        "soak_reply_count": _nonnegative_int(smoke.get("soak_reply_count"), 0),
        "soak_check_statuses": _safe_strings(smoke.get("soak_check_statuses"), limit=32),
        "start_status": _result_status(smoke.get("start_status")),
        "check_status": _result_status(smoke.get("check_status")),
        "resume_status": _result_status(smoke.get("resume_status")),
        "restart_status": _result_status(smoke.get("restart_status")),
        "recover_status": _result_status(smoke.get("recover_status")),
        "stop_status": _result_status(smoke.get("stop_status")),
        "post_stop_process_status": _result_status(smoke.get("post_stop_process_status")),
    }
    if str(smoke.get("error") or "").strip():
        safe["error"] = SESSION_SMOKE_ERROR
    reason = clean_lobby_text(smoke.get("reason"), limit=128)
    if reason:
        safe["reason"] = reason
    return safe


def safe_readiness_probe_groups(
    probe_groups: list[dict[str, object]],
    *,
    include_agent_ids: bool,
) -> list[dict[str, object]]:
    safe_groups = []
    for group in probe_groups:
        safe_group: dict[str, object] = {
            "status": str(group.get("status") or "unknown"),
            "group_id": str(group.get("group_id") or ""),
        }
        agent_ids = payload_probe_ids(group.get("agent_ids"))
        if agent_ids and include_agent_ids:
            safe_group["agent_ids"] = agent_ids
        elif agent_ids:
            safe_group["agent_count"] = len(agent_ids)
        reason = str(group.get("reason") or "").strip()[:128]
        if reason:
            safe_group["reason"] = reason
        safe_groups.append(safe_group)
    return safe_groups


def safe_readiness_probe_result(probe: dict[str, object]) -> dict[str, object]:
    safe = {
        "status": str(probe.get("status") or "unknown"),
        "agent_id": str(probe.get("agent_id") or ""),
    }
    for key in ("agent_status", "reason", "source_event_id", "reply_event_id"):
        value = str(probe.get(key) or "")
        if value:
            safe[key] = value[:128]
    return safe


def payload_probe_ids(value: object) -> list[str]:
    raw_items = value if isinstance(value, list) else []
    values = []
    seen = set()
    for item in raw_items:
        if not isinstance(item, str):
            continue
        clean_value = item.strip()[:64]
        if not clean_value or clean_value in seen:
            continue
        seen.add(clean_value)
        values.append(clean_value)
    return values


def readiness_operation_details(
    readiness: dict[str, object],
    payload: dict[str, object],
) -> dict[str, object]:
    basic_smoke = readiness.get("smoke") if isinstance(readiness.get("smoke"), dict) else {}
    official = readiness.get("official_round_smoke")
    official_smoke = official if isinstance(official, dict) else {}
    session = readiness.get("session_smoke")
    session_smoke = session if isinstance(session, dict) else {}
    probes = readiness.get("probes") if isinstance(readiness.get("probes"), list) else []
    probe_groups = readiness.get("probe_groups") if isinstance(readiness.get("probe_groups"), list) else []
    return {
        "group_id": str(basic_smoke.get("group_id") or payload.get("group_id") or ""),
        "result_status": _result_status(readiness.get("status")),
        **readiness_health_operation_details(readiness.get("health")),
        "probe_agent_ids": payload_probe_ids(payload.get("probe_agent_ids")),
        "probe_group_ids": payload_probe_ids(payload.get("probe_group_ids")),
        "effective_probe_agent_ids": payload_probe_ids(readiness.get("effective_probe_agent_ids")),
        "probe_error": str(readiness.get("probe_error") or ""),
        "probe_group_statuses": _probe_group_statuses(probe_groups),
        "probe_statuses": _probe_statuses(probes),
        "official_round_smoke": _result_status(official_smoke.get("status")),
        "official_round_answered_count": _nonnegative_int(official_smoke.get("answered_count"), 0),
        "official_round_timeout_count": _nonnegative_int(official_smoke.get("timeout_count"), 0),
        "official_round_skipped_count": _nonnegative_int(official_smoke.get("skipped_count"), 0),
        "session_smoke": _result_status(session_smoke.get("status")),
        "session_smoke_terminal_session_status": _result_status(session_smoke.get("terminal_session_status")),
        "session_smoke_terminal_session_included": session_smoke.get("terminal_session_included") is True,
        "session_smoke_finalization_status": _result_status(session_smoke.get("finalization_status")),
        "session_smoke_finalization_official_event_count": _nonnegative_int(
            session_smoke.get("finalization_official_event_count"), 0
        ),
        "session_smoke_return_packet_event_count": _nonnegative_int(
            session_smoke.get("return_packet_event_count"), 0
        ),
        "session_smoke_artifact_status": _result_status(session_smoke.get("artifact_status")),
        "session_smoke_self_service_official_reply_count": _nonnegative_int(
            session_smoke.get("self_service_official_reply_count"), 0
        ),
        "session_smoke_self_service_lobby_reply_count": _nonnegative_int(
            session_smoke.get("self_service_lobby_reply_count"), 0
        ),
        "session_smoke_self_service_post_restart_reply_count": _nonnegative_int(
            session_smoke.get("self_service_post_restart_reply_count"), 0
        ),
        "session_smoke_self_service_post_recover_reply_count": _nonnegative_int(
            session_smoke.get("self_service_post_recover_reply_count"), 0
        ),
        "session_smoke_self_service_soak_reply_count": _nonnegative_int(
            session_smoke.get("self_service_soak_reply_count"), 0
        ),
        "session_smoke_reply_count": _nonnegative_int(session_smoke.get("reply_count"), 0),
        "session_smoke_post_restart_reply_count": _nonnegative_int(
            session_smoke.get("post_restart_reply_count"), 0
        ),
        "session_smoke_post_recover_reply_count": _nonnegative_int(
            session_smoke.get("post_recover_reply_count"), 0
        ),
        "session_smoke_soak_cycle_count": _nonnegative_int(session_smoke.get("soak_cycle_count"), 0),
        "session_smoke_soak_reply_count": _nonnegative_int(session_smoke.get("soak_reply_count"), 0),
        "session_smoke_soak_check_statuses": _safe_strings(session_smoke.get("soak_check_statuses"), limit=32),
        "session_smoke_post_stop_process_status": _result_status(
            session_smoke.get("post_stop_process_status")
        ),
        "session_smoke_recover_status": _result_status(session_smoke.get("recover_status")),
    }


def readiness_health_operation_details(health: object) -> dict[str, object]:
    if not isinstance(health, dict):
        return {}
    details: dict[str, object] = {"health_status": _result_status(health.get("status"))}
    detail_names = {
        "agents": "agent",
        "processes": "process",
        "connections": "connection",
        "sessions": "session",
    }
    for section_name, detail_name in detail_names.items():
        section = health.get(section_name)
        if not isinstance(section, dict):
            continue
        attention = _safe_health_operation_strings(section.get("attention"), limit=128)
        if attention:
            details[f"health_{detail_name}_attention"] = attention
    long_session_sections = {
        "observations": ("observation", ("lobby_behind_count", "live_behind_count", "error_count")),
        "shared_memory": ("shared_memory", ("ready_sessions", "with_memory")),
        "session_runs": ("session_run", ("active", "retrying")),
        "session_run_monitor": ("session_run_monitor", ("last_result_count",)),
    }
    for section_name, (detail_name, count_names) in long_session_sections.items():
        section = health.get(section_name)
        if not isinstance(section, dict):
            continue
        attention = _safe_health_operation_strings(section.get("attention"), limit=128)
        if attention:
            details[f"health_{detail_name}_attention"] = attention
        for count_name in count_names:
            count = _nonnegative_int(section.get(count_name), 0)
            if count:
                details[f"health_{detail_name}_{count_name}"] = count
    process_reasons = _health_process_reason_labels(health.get("processes"))
    if process_reasons:
        details["health_process_reasons"] = process_reasons
    return details


def _probe_statuses(probes: object) -> list[str]:
    if not isinstance(probes, list):
        return []
    statuses = []
    for probe in probes:
        if not isinstance(probe, dict):
            continue
        agent_id = str(probe.get("agent_id") or "").strip()
        status = _result_status(probe.get("status"))
        if agent_id:
            statuses.append(f"{agent_id}:{status}")
    return statuses


def _probe_group_statuses(probe_groups: object) -> list[str]:
    if not isinstance(probe_groups, list):
        return []
    statuses = []
    for group in probe_groups:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("group_id") or "").strip()
        status = _result_status(group.get("status"))
        if group_id:
            statuses.append(f"{group_id}:{status}")
    return statuses


def _health_process_reason_labels(processes: object) -> list[str]:
    if not isinstance(processes, dict):
        return []
    reasons = processes.get("reasons")
    if not isinstance(reasons, dict):
        return []
    labels = []
    for group_id, reason_payload in reasons.items():
        clean_group_id = clean_lobby_text(group_id, limit=64)
        if not clean_group_id:
            continue
        if isinstance(reason_payload, dict):
            event_type = clean_lobby_text(reason_payload.get("event_type"), limit=64)
            reason = clean_lobby_text(reason_payload.get("reason"), limit=160)
        else:
            event_type = ""
            reason = clean_lobby_text(reason_payload, limit=160)
        label = " ".join(part for part in (clean_group_id, event_type, reason) if part)
        if label and not looks_sensitive_operator_diagnostic_text(label):
            labels.append(label)
    return labels


def _safe_health_operation_strings(value: object, *, limit: int) -> list[str]:
    return [
        text
        for text in _safe_strings(value, limit=limit)
        if not looks_sensitive_operator_diagnostic_text(text)
    ]


def _safe_strings(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    strings = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = clean_lobby_text(item, limit=limit)
        if text:
            strings.append(text)
    return strings


def _nonnegative_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (OverflowError, TypeError, ValueError):
        return default
    return max(0, parsed)


def _nonnegative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (OverflowError, TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(0.0, parsed)


def _result_status(value: object) -> str:
    return str(value or "unknown").strip() or "unknown"
