"""Bounded operation-audit projections for retained meeting commands."""
from __future__ import annotations

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
    return [cleaned for item in value if (cleaned := clean_lobby_text(item, limit=limit))]


def _item_count(value: object) -> int:
    return len(value) if isinstance(value, list) else 0
