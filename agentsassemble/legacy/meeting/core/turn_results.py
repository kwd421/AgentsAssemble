"""Result normalization shared by retained sequential turn commands."""
from __future__ import annotations

import math


def turn_sequence_result(index: int, result: dict[str, object]) -> dict[str, object]:
    request_event = result.get("request_event") if isinstance(result.get("request_event"), dict) else {}
    reply_event = result.get("reply_event") if isinstance(result.get("reply_event"), dict) else None
    return {
        "index": index,
        "agent_id": str(request_event.get("target_agent_id") or ""),
        "role_id": str(request_event.get("role_id") or ""),
        "status": str(result.get("status") or "unknown"),
        "request_event": request_event,
        "reply_event": reply_event,
        "elapsed_seconds": _nonnegative_float(result.get("elapsed_seconds"), 0.0),
        "timeout_seconds": _nonnegative_float(result.get("timeout_seconds"), 0.0),
    }


def turn_sequence_status(
    answered_count: int,
    timeout_count: int,
    skipped_count: int,
    cancelled_count: int,
    *,
    turn_count: int,
) -> str:
    if skipped_count:
        return "stopped"
    if timeout_count:
        return "timeout"
    if cancelled_count:
        return "cancelled"
    if answered_count == turn_count:
        return "answered"
    return "degraded"


def _nonnegative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(0.0, parsed)
