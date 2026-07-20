"""Safe status projection policy for the retained legacy resident runtime."""

from __future__ import annotations

import math
import re
from pathlib import Path

from agentsassemble.meeting_events import clean_lobby_text


DEFAULT_SESSION_RUN_MONITOR_INTERVAL_SECONDS = 30.0
MIN_SESSION_RUN_MONITOR_INTERVAL_SECONDS = 1.0
HEALTH_WATCHDOG_REASON_EVENT_TYPES = {"stale_watchdog", "stale_watchdog_stop_failed"}
HEALTH_RESTART_FAILED_REASON_EVENT_TYPE = "restart_failed"
HEALTH_RECOVERED_UNKNOWN_REASON_EVENT_TYPE = "recovered_unknown"
HEALTH_RECOVERED_UNKNOWN_REASON = "orphan running record marked unknown"
LIVE_AGENT_ADMISSION_HEALTH_STATUSES = (
    "bound_to_meeting",
    "binding_conflict",
    "meeting_lobby_only",
    "meeting_missing",
    "lobby_only",
    "unknown",
)
SAFE_HEALTH_WATCHDOG_REASON_PATTERN = re.compile(
    r"^(?:(?:missing|stale|offline|error) manifest agent|wrong meeting manifest agent) [A-Za-z0-9_.-]{1,64}$"
)
SAFE_HEALTH_RESTART_FAILED_GROUP_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SAFE_HEALTH_RESTART_FAILED_ERROR_PATTERN = re.compile(
    r"Restart failed: Live agent group ([A-Za-z0-9_.-]{1,64}) has no (config|server) to (?:restart|recover)\."
)


def live_agent_status_summary(agents: list[dict[str, object]]) -> dict[str, object]:
    visible_agents = [agent for agent in agents if not is_diagnostic_agent(agent)]
    counts = {"online": 0, "working": 0, "error": 0, "stale": 0, "offline": 0}
    attention = []
    for index, agent in enumerate(visible_agents, start=1):
        raw_status = str(agent.get("status") or "offline")
        status = raw_status if raw_status in counts else "offline"
        counts[status] += 1
        if status in {"error", "stale", "offline"}:
            attention.append(safe_health_identity(agent.get("agent_id")) or f"missing-agent-id-{index}")
    return {
        "total": len(visible_agents),
        "live": counts["online"] + counts["working"],
        "counts": counts,
        "attention": attention,
    }


def live_agent_process_status_summary(
    groups: list[dict[str, object]],
    *,
    diagnostic_group_ids: set[str] | None = None,
) -> dict[str, object]:
    diagnostic_group_ids = diagnostic_group_ids or set()
    visible_groups = [group for group in groups if not is_diagnostic_process_group(group, diagnostic_group_ids)]
    counts = {"running": 0, "restarting": 0, "error": 0, "unknown": 0, "stopped": 0}
    attention = []
    meeting_ids = {}
    reasons = {}
    for index, group in enumerate(visible_groups, start=1):
        raw_status = str(group.get("status") or "unknown")
        status = raw_status if raw_status in counts else "unknown"
        counts[status] += 1
        group_id = safe_process_group_id(group.get("group_id"), fallback=f"missing-process-group-id-{index}")
        meeting_id = safe_health_identity(group.get("meeting_id"))
        if group_id and meeting_id:
            meeting_ids[group_id] = meeting_id
        if status in {"restarting", "error", "unknown", "stopped"}:
            attention.append(group_id)
            reason = live_agent_process_health_reason(group)
            if reason:
                reasons[group_id] = reason
    return {
        "total": len(visible_groups),
        "counts": counts,
        "attention": attention,
        "meeting_ids": meeting_ids,
        "reasons": reasons,
    }


def live_agent_process_monitor_summary(process_supervisor: object) -> dict[str, object]:
    snapshot_fn = getattr(process_supervisor, "monitor_snapshot", None)
    if not callable(snapshot_fn):
        return {}
    try:
        raw = snapshot_fn()
    except Exception as error:
        raw = {
            "running": False,
            "interval_seconds": 0,
            "last_tick_at": "",
            "last_status": "failed",
            "last_group_count": 0,
            "last_error_type": _safe_exception_type(error),
        }
    last_status = safe_monitor_status(raw.get("last_status"))
    last_error_type = safe_monitor_error_type(raw.get("last_error_type"))
    attention = []
    if last_status == "failed":
        attention.append(f"failed:{last_error_type or 'Exception'}")
    return {
        "running": raw.get("running") is True,
        "interval_seconds": safe_monitor_interval(raw.get("interval_seconds")),
        "last_tick_at": safe_health_timestamp(raw.get("last_tick_at")),
        "last_status": last_status,
        "last_group_count": safe_health_int(raw.get("last_group_count")),
        "last_error_type": last_error_type,
        "attention": attention,
    }


def safe_monitor_status(value: object) -> str:
    status = clean_lobby_text(value, limit=64)
    return status if status in {"not_started", "ok", "failed"} else "unknown"


def safe_monitor_error_type(value: object) -> str:
    error_type = clean_lobby_text(value, limit=80)
    return error_type if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,79}", error_type) else ""


def safe_monitor_interval(value: object) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(seconds):
        return 0.0
    return max(0.01, seconds)


def live_agent_process_health_reason(group: dict[str, object]) -> dict[str, str]:
    events = group.get("recent_events") if isinstance(group.get("recent_events"), list) else []
    group_id = str(group.get("group_id") or "").strip()
    status = str(group.get("status") or "").strip()
    seen_newer_event = False
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "").strip()
        if event_type in HEALTH_WATCHDOG_REASON_EVENT_TYPES:
            reason = _safe_watchdog_reason(event.get("reason"))
        elif event_type == HEALTH_RESTART_FAILED_REASON_EVENT_TYPE:
            if seen_newer_event or status != "error":
                continue
            reason = _safe_restart_failed_reason(group.get("last_error"), group_id=group_id)
        elif event_type == HEALTH_RECOVERED_UNKNOWN_REASON_EVENT_TYPE:
            if seen_newer_event or status != "unknown":
                continue
            reason = HEALTH_RECOVERED_UNKNOWN_REASON
        else:
            seen_newer_event = True
            continue
        if reason:
            return {"event_type": event_type, "reason": reason}
    return {}


def safe_health_reason(value: object) -> str:
    reason = clean_lobby_text(value, limit=64)
    if not reason or looks_sensitive_health_text(reason):
        return "current_readiness_degraded"
    return reason if re.fullmatch(r"[A-Za-z0-9_:-]{1,64}", reason) else "current_readiness_degraded"


def safe_health_identity(value: object) -> str:
    text = clean_lobby_text(value, limit=128)
    if not text or text in {".", ".."}:
        return ""
    if text.casefold().startswith(("env:", "literal:")):
        return ""
    if looks_sensitive_health_text(text):
        return ""
    if "/" in text or "\\" in text or Path(text).name != text:
        return ""
    return text


def safe_health_phase(value: object) -> str:
    text = clean_lobby_text(value, limit=128)
    if not text or looks_sensitive_health_text(text):
        return ""
    return text if re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", text) else ""


def looks_sensitive_health_text(text: str) -> bool:
    token_like = re.search(
        r"\b(?:sk-[A-Za-z0-9_-]{6,}|gh[opusr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
        text,
    )
    return bool(token_like) or _looks_sensitive_process_control_error(text) or "literal:" in text.casefold()


def safe_health_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def safe_health_timestamp(value: object) -> str:
    timestamp = clean_lobby_text(value, limit=64)
    return timestamp if re.fullmatch(r"[0-9T:+.\-Z]{1,64}", timestamp) else ""


def safe_process_group_id(value: object, *, fallback: str) -> str:
    return safe_health_identity(value) or fallback


def diagnostic_agent_group_ids(agents: list[dict[str, object]]) -> set[str]:
    by_group: dict[str, set[str]] = {}
    for agent in agents:
        group_id, smoke_role = _smoke_agent_identity(agent)
        if group_id:
            by_group.setdefault(group_id, set()).add(smoke_role)
    return {group_id for group_id, roles in by_group.items() if {"local_cli", "live_session"}.issubset(roles)}


def is_diagnostic_agent(agent: dict[str, object]) -> bool:
    return _payload_bool(agent.get("diagnostic")) or bool(_smoke_group_id_from_agent(agent))


def is_diagnostic_process_group(group: dict[str, object], diagnostic_group_ids: set[str]) -> bool:
    return _payload_bool(group.get("diagnostic")) or _is_legacy_smoke_process_group(group, diagnostic_group_ids)


def _is_legacy_smoke_process_group(group: dict[str, object], diagnostic_group_ids: set[str]) -> bool:
    group_id = str(group.get("group_id") or "")
    if group_id not in diagnostic_group_ids:
        return False
    if str(group.get("status") or "") != "stopped" or group.get("returncode") not in (0, None):
        return False
    config_path = str(group.get("config_path") or "")
    return bool(config_path) and not Path(config_path).exists()


def _smoke_group_id_from_agent(agent: dict[str, object]) -> str:
    group_id, _ = _smoke_agent_identity(agent)
    return group_id


def _smoke_agent_identity(agent: dict[str, object]) -> tuple[str, str]:
    if str(agent.get("provider_kind") or "") != "local_cli":
        return "", ""
    agent_id = str(agent.get("agent_id") or "")
    display_name = str(agent.get("display_name") or "")
    connection_kind = str(agent.get("connection_kind") or "")
    if display_name == "Smoke Local CLI" and connection_kind == "local_cli" and agent_id.endswith("-local-cli"):
        return agent_id[: -len("-local-cli")], "local_cli"
    if display_name == "Smoke Live Session" and connection_kind == "live_session" and agent_id.endswith("-live-session"):
        return agent_id[: -len("-live-session")], "live_session"
    return "", ""


def _safe_watchdog_reason(value: object) -> str:
    reason = clean_lobby_text(value, limit=160)
    if not reason or looks_sensitive_health_text(reason) or "/" in reason or "\\" in reason:
        return ""
    lowered = reason.casefold()
    if ".json" in lowered or "env:" in lowered:
        return ""
    return reason if SAFE_HEALTH_WATCHDOG_REASON_PATTERN.fullmatch(reason) else ""


def _safe_restart_failed_reason(value: object, *, group_id: str) -> str:
    if not SAFE_HEALTH_RESTART_FAILED_GROUP_ID_PATTERN.fullmatch(group_id):
        return ""
    error = clean_lobby_text(value, limit=240)
    if not error or _looks_sensitive_restart_failed_error(error):
        return ""
    match = SAFE_HEALTH_RESTART_FAILED_ERROR_PATTERN.search(error)
    if not match or match.group(1) != group_id:
        return ""
    return "missing launch config" if match.group(2) == "config" else "missing launch server"


def _looks_sensitive_restart_failed_error(error: str) -> bool:
    lowered = error.casefold()
    return (
        bool(re.search(r"\b(auth|credential|password|secret|token)\b", lowered))
        or bool(re.search(r"(^|[\s:=])/", error))
        or "\\" in error
        or "://" in error
        or "--" in error
        or ".json" in lowered
        or "env:" in lowered
    )


def _looks_sensitive_process_control_error(message: str) -> bool:
    lowered = message.casefold()
    markers = (
        "authorization",
        "bearer ",
        "secret",
        "token",
        "api-key",
        "apikey",
        "x-api-key",
        "password",
        "http://",
        "https://",
        "env:",
        ".json",
        ".env",
        ".toml",
    )
    if any(marker in lowered for marker in markers):
        return True
    return "\\" in message or "--" in message or bool(re.search(r"(^|[\s=])(?:/|~/|\./|\.\./)\S+", message))


def _payload_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}


def _safe_exception_type(error: Exception) -> str:
    error_type = clean_lobby_text(type(error).__name__, limit=80)
    return error_type if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,79}", error_type) else "Exception"
