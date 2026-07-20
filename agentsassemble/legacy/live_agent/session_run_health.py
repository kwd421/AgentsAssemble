"""Durable session-run and monitor health projections for legacy residents."""

from __future__ import annotations

import math
import re
from pathlib import Path

from agentsassemble.legacy.live_agent.diagnostics import (
    session_readiness_by_target,
    session_run_readiness_overlay,
)
from agentsassemble.legacy.live_agent.health import (
    DEFAULT_SESSION_RUN_MONITOR_INTERVAL_SECONDS,
    MIN_SESSION_RUN_MONITOR_INTERVAL_SECONDS,
    safe_health_identity,
    safe_health_int,
    safe_health_phase,
    safe_health_reason,
    safe_health_timestamp,
)
from agentsassemble.legacy.live_agent.runtime.session_runs import LiveAgentSessionRunController
from agentsassemble.legacy.meeting.core.events import clean_lobby_text
from agentsassemble.application.session_run_monitor import PeriodicSessionRunMonitor


def live_agent_session_run_health_summary(
    output_root: Path,
    *,
    session_summary: dict[str, object] | None = None,
) -> dict[str, object]:
    snapshot = LiveAgentSessionRunController(output_root).health_snapshot()
    runs = snapshot.get("runs") if isinstance(snapshot.get("runs"), list) else []
    readiness_by_target = session_readiness_by_target(session_summary or {})
    items = []
    attention = []
    retrying_count = 0
    for run in runs:
        if not isinstance(run, dict):
            continue
        status = str(run.get("status") or "unknown").strip() or "unknown"
        active = run.get("active") is True
        retrying = _live_agent_session_run_retrying(run)
        if retrying:
            retrying_count += 1
        readiness = session_run_readiness_overlay(run, readiness_by_target) if active else {}
        readiness_issue = _live_agent_session_run_readiness_issue(readiness) if active and status == "ready" else ""
        if active and status != "ready":
            attention.append(_live_agent_session_run_attention_label(run, status=status, retrying=retrying))
        elif readiness_issue:
            attention.append(_live_agent_session_run_attention_label(run, status=status, reason=readiness_issue))
        item = {
            "run_id": safe_health_identity(run.get("run_id")),
            "meeting_id": safe_health_identity(run.get("meeting_id")),
            "group_id": safe_health_identity(run.get("group_id")),
            "status": clean_lobby_text(status, limit=64),
            "active": active,
            "phase": safe_health_phase(run.get("phase")),
            "reconcile_failure_count": safe_health_int(run.get("reconcile_failure_count")),
            "reconcile_backoff_seconds": safe_health_int(run.get("reconcile_backoff_seconds")),
            "next_reconcile_at": safe_health_timestamp(run.get("next_reconcile_at")),
        }
        if active:
            item["readiness"] = readiness
        items.append(item)
    return {
        "total": safe_health_int(snapshot.get("total")),
        "active": safe_health_int(snapshot.get("active")),
        "ready": safe_health_int(snapshot.get("ready")),
        "retrying": retrying_count,
        "attention": attention,
        "items": items,
    }


def live_agent_session_run_monitor_health_summary(
    monitor: PeriodicSessionRunMonitor | None,
) -> dict[str, object]:
    if monitor is None:
        return {}
    raw = monitor.snapshot()
    last_status = _safe_session_run_monitor_status(raw.get("last_status"))
    last_error_type = _safe_session_run_monitor_error_type_value(raw.get("last_error_type"))
    attention = []
    if last_status == "failed":
        attention.append(f"failed:{last_error_type or 'Exception'}")
    return {
        "running": raw.get("running") is True,
        "interval_seconds": _safe_session_run_monitor_interval_value(raw.get("interval_seconds")),
        "last_tick_at": safe_health_timestamp(raw.get("last_tick_at")),
        "last_status": last_status,
        "last_result_count": safe_health_int(raw.get("last_result_count")),
        "last_error_type": last_error_type,
        "attention": attention,
    }


def _safe_session_run_monitor_status(value: object) -> str:
    status = clean_lobby_text(value, limit=64)
    return status if status in {"not_started", "ok", "degraded", "failed"} else "unknown"


def _safe_session_run_monitor_error_type_value(value: object) -> str:
    error_type = clean_lobby_text(value, limit=80)
    return error_type if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,79}", error_type) else ""


def _safe_session_run_monitor_interval_value(value: object) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_SESSION_RUN_MONITOR_INTERVAL_SECONDS
    if not math.isfinite(seconds):
        return DEFAULT_SESSION_RUN_MONITOR_INTERVAL_SECONDS
    return max(MIN_SESSION_RUN_MONITOR_INTERVAL_SECONDS, seconds)


def _live_agent_session_run_retrying(run: dict[str, object]) -> bool:
    return (
        safe_health_int(run.get("reconcile_failure_count")) > 0
        or safe_health_int(run.get("reconcile_backoff_seconds")) > 0
        or bool(safe_health_timestamp(run.get("next_reconcile_at")))
    )


def _live_agent_session_run_readiness_issue(readiness: dict[str, object]) -> str:
    if clean_lobby_text(readiness.get("status"), limit=64) == "ready":
        return ""
    attention = readiness.get("attention") if isinstance(readiness.get("attention"), list) else []
    if "session_run:no_current_readiness" in attention:
        return "no_current_readiness"
    if "session_run:missing_target" in attention:
        return "missing_target"
    process_status = clean_lobby_text(readiness.get("process_status"), limit=64)
    if process_status and process_status != "running":
        return f"process_{process_status}"
    return "current_readiness_degraded"


def _live_agent_session_run_attention_label(
    run: dict[str, object],
    *,
    status: str,
    retrying: bool = False,
    reason: str = "",
) -> str:
    parts = [
        safe_health_identity(run.get("meeting_id")) or "-",
        safe_health_identity(run.get("group_id")) or "-",
        safe_health_identity(run.get("run_id")) or "-",
        clean_lobby_text(status, limit=64) or "unknown",
    ]
    if reason:
        parts.append(safe_health_reason(reason))
    elif retrying:
        parts.append("retrying")
    return ":".join(parts)
