"""Bounded operation-audit projections for retained meeting commands."""
from __future__ import annotations

import math

from agentsassemble.live_agent_processes import clean_live_agent_group_id
from agentsassemble.meeting_events import clean_lobby_text


def meeting_finalize_operation_details(result: dict[str, object], meeting_id: str) -> dict[str, object]:
    details = {
        "result_status": _operation_result_status(result.get("status")),
        "meeting_id": clean_lobby_text(result.get("meeting_id") or meeting_id, limit=128),
        "official_event_count": _nonnegative_int(result.get("official_event_count"), 0),
        "artifact_event_id": clean_lobby_text(result.get("artifact_event_id"), limit=128),
        "cancelled_pending_count": _nonnegative_int(result.get("cancelled_pending_count"), 0),
        "cancelled_event_ids": _safe_strings(result.get("cancelled_event_ids"), limit=128),
        "cancelled_turn_request_ids": _safe_strings(result.get("cancelled_turn_request_ids"), limit=128),
    }
    shared_memory = result.get("shared_memory") if isinstance(result.get("shared_memory"), dict) else {}
    if shared_memory:
        details.update(shared_memory_operation_details(shared_memory))
    return details


def shared_memory_operation_details(memory: dict[str, object]) -> dict[str, object]:
    return {
        "shared_memory_official_event_count": _nonnegative_int(memory.get("official_event_count"), 0),
        "shared_memory_last_event_id": clean_lobby_text(memory.get("last_official_event_id"), limit=128),
        "shared_memory_decision_count": _nonnegative_int(
            memory.get("decision_count"),
            _item_count(memory.get("decisions")),
        ),
        "shared_memory_open_question_count": _nonnegative_int(
            memory.get("open_question_count"),
            _item_count(memory.get("open_questions")),
        ),
        "shared_memory_action_item_count": _nonnegative_int(
            memory.get("action_item_count"),
            _item_count(memory.get("action_items")),
        ),
    }


def turn_sequence_operation_details(sequence: dict[str, object], meeting_id: str) -> dict[str, object]:
    results = sequence.get("results") if isinstance(sequence.get("results"), list) else []
    request_event_ids: list[str] = []
    reply_event_ids: list[str] = []
    agent_ids: list[str] = []
    statuses: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        request_event = item.get("request_event") if isinstance(item.get("request_event"), dict) else {}
        reply_event = item.get("reply_event") if isinstance(item.get("reply_event"), dict) else {}
        if request_event.get("id"):
            request_event_ids.append(str(request_event.get("id") or ""))
        if reply_event.get("id"):
            reply_event_ids.append(str(reply_event.get("id") or ""))
        if item.get("agent_id"):
            agent_ids.append(str(item.get("agent_id") or ""))
        if item.get("status"):
            statuses.append(str(item.get("status") or "unknown"))
    return {
        "meeting_id": meeting_id,
        "turn_count": _nonnegative_int(sequence.get("turn_count"), 0),
        "answered_count": _nonnegative_int(sequence.get("answered_count"), 0),
        "timeout_count": _nonnegative_int(sequence.get("timeout_count"), 0),
        "skipped_count": _nonnegative_int(sequence.get("skipped_count"), 0),
        "cancelled_count": _nonnegative_int(sequence.get("cancelled_count"), 0),
        "stopped": sequence.get("stopped") is True,
        "agent_ids": agent_ids,
        "statuses": statuses,
        "request_event_ids": request_event_ids,
        "reply_event_ids": reply_event_ids,
        "timeout_seconds": _nonnegative_float(sequence.get("timeout_seconds"), 0.0),
    }


def review_checkpoint_operation_details(checkpoint: dict[str, object], meeting_id: str) -> dict[str, object]:
    details = turn_sequence_operation_details(checkpoint, meeting_id)
    details["result_status"] = _operation_result_status(checkpoint.get("status"))
    details["checkpoint_id"] = clean_lobby_text(checkpoint.get("checkpoint_id"), limit=128)
    details["group_id"] = clean_lobby_text(checkpoint.get("group_id"), limit=128)
    reason = clean_lobby_text(checkpoint.get("reason"), limit=128)
    if reason:
        details["reason"] = reason
    expected_agent_ids = _safe_strings(checkpoint.get("expected_agent_ids"), limit=64)
    if expected_agent_ids:
        details["expected_agent_ids"] = expected_agent_ids
    return details


def review_checkpoint_request_operation_details(
    payload: dict[str, object],
    meeting_id: str,
) -> dict[str, object]:
    return {
        "meeting_id": meeting_id,
        "group_id": clean_live_agent_group_id(str(payload.get("group_id") or "")),
        "checkpoint_id": clean_lobby_text(
            payload.get("checkpoint_id") or payload.get("review_checkpoint_id"),
            limit=128,
        ),
        "agent_ids": _safe_strings(payload.get("agent_ids"), limit=64),
        "timeout_seconds": _nonnegative_float(
            payload.get("timeout_seconds", payload.get("timeout")),
            30.0,
        ),
    }


def _operation_result_status(value: object) -> str:
    return str(value or "unknown").strip() or "unknown"


def _nonnegative_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (OverflowError, TypeError, ValueError):
        return default
    return max(0, parsed)


def _safe_strings(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        cleaned
        for item in value
        if isinstance(item, str) and (cleaned := clean_lobby_text(item, limit=limit))
    ]


def _nonnegative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(0.0, parsed)


def _item_count(value: object) -> int:
    return len(value) if isinstance(value, list) else 0
