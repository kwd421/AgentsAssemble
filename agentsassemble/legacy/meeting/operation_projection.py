"""Bounded operation-audit projections for retained meeting commands."""
from __future__ import annotations

import math

from agentsassemble.legacy.live_agent.runtime.processes import clean_live_agent_group_id
from agentsassemble.legacy.meeting.core.events import clean_lobby_text


SHARED_MEMORY_OPERATION_DETAIL_KEYS = (
    "shared_memory_official_event_count",
    "shared_memory_last_event_id",
    "shared_memory_decision_count",
    "shared_memory_open_question_count",
    "shared_memory_action_item_count",
)


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


def official_reply_request_operation_details(
    payload: dict[str, object],
) -> dict[str, object]:
    return {
        "meeting_id": clean_lobby_text(payload.get("meeting_id"), limit=128),
        "source_event_id": clean_lobby_text(payload.get("source_event_id"), limit=128),
        "role_id": clean_lobby_text(payload.get("role_id"), limit=128),
        "turn_id": clean_lobby_text(payload.get("turn_id"), limit=128),
        "turn_index": _optional_int(payload.get("turn_index")),
    }


def official_reply_operation_details(
    event: dict[str, object],
    payload: dict[str, object],
    shared_memory: dict[str, object],
) -> dict[str, object]:
    details = {
        "meeting_id": clean_lobby_text(
            event.get("meeting_id") or payload.get("meeting_id"),
            limit=128,
        ),
        "source_event_id": clean_lobby_text(event.get("source_event_id"), limit=128),
        "role_id": clean_lobby_text(event.get("role_id"), limit=128),
        "turn_id": clean_lobby_text(event.get("turn_id"), limit=128),
        "turn_index": _optional_int(event.get("turn_index")),
    }
    review_checkpoint_id = clean_lobby_text(event.get("review_checkpoint_id"), limit=128)
    if review_checkpoint_id:
        details["review_checkpoint_id"] = review_checkpoint_id
    details.update(
        {
            key: shared_memory[key]
            for key in SHARED_MEMORY_OPERATION_DETAIL_KEYS
            if key in shared_memory
        }
    )
    return details


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


def official_turn_request_operation_details(
    source: dict[str, object],
    meeting_id: str,
    *,
    fallback_agent_id: str,
    include_source_event: bool = False,
) -> dict[str, object]:
    details = {
        "meeting_id": meeting_id,
        "target_agent_id": str(source.get("target_agent_id") or fallback_agent_id),
        "role_id": str(source.get("role_id") or ""),
        "turn_id": str(source.get("turn_id") or ""),
        "turn_index": _optional_int(source.get("turn_index")),
    }
    if include_source_event:
        details["source_event_id"] = str(source.get("id") or "")
    return details


def official_turn_call_request_operation_details(
    payload: dict[str, object],
    meeting_id: str,
    *,
    fallback_agent_id: str,
) -> dict[str, object]:
    details = official_turn_request_operation_details(
        payload,
        meeting_id,
        fallback_agent_id=fallback_agent_id,
    )
    details["timeout_seconds"] = _nonnegative_float(
        payload.get("timeout_seconds", payload.get("timeout")),
        30.0,
    )
    return details


def official_turn_call_operation_details(
    result: dict[str, object],
    meeting_id: str,
    *,
    fallback_agent_id: str,
) -> dict[str, object]:
    request_event = result.get("request_event") if isinstance(result.get("request_event"), dict) else {}
    reply_event = result.get("reply_event") if isinstance(result.get("reply_event"), dict) else {}
    details = official_turn_request_operation_details(
        request_event,
        meeting_id,
        fallback_agent_id=fallback_agent_id,
        include_source_event=True,
    )
    details.update(
        {
            "reply_event_id": str(reply_event.get("id") or ""),
            "timeout_seconds": _nonnegative_float(result.get("timeout_seconds"), 30.0),
            "elapsed_seconds": _nonnegative_float(result.get("elapsed_seconds"), 0.0),
        }
    )
    return details


def official_turn_sequence_request_operation_details(
    payload: dict[str, object],
    meeting_id: str,
) -> dict[str, object]:
    turns = payload.get("turns")
    return {
        "meeting_id": meeting_id,
        "turn_count": len(turns) if isinstance(turns, list) else 0,
        "timeout_seconds": _nonnegative_float(
            payload.get("timeout_seconds", payload.get("timeout")),
            30.0,
        ),
        "stop_on_timeout": _payload_bool(payload.get("stop_on_timeout")),
    }


def official_round_operation_details(
    round_result: dict[str, object],
    meeting_id: str,
) -> dict[str, object]:
    details = turn_sequence_operation_details(round_result, meeting_id)
    details["round_id"] = clean_lobby_text(round_result.get("round_id"), limit=128)
    details["role_ids"] = _safe_strings(round_result.get("role_ids"), limit=128)
    return details


def official_round_request_operation_details(
    payload: dict[str, object],
    meeting_id: str,
) -> dict[str, object]:
    return {
        "meeting_id": meeting_id,
        "round_id": clean_lobby_text(payload.get("round_id"), limit=128),
        "role_ids": _safe_strings(payload.get("role_ids"), limit=128),
        "timeout_seconds": _nonnegative_float(
            payload.get("timeout_seconds", payload.get("timeout")),
            30.0,
        ),
        "stop_on_timeout": _payload_bool(payload.get("stop_on_timeout")),
    }


def official_rounds_request_operation_details(
    payload: dict[str, object],
    meeting_id: str,
    *,
    max_rounds: int,
) -> dict[str, object]:
    return {
        "meeting_id": meeting_id,
        "timeout_seconds": _nonnegative_float(
            payload.get("timeout_seconds", payload.get("timeout")),
            30.0,
        ),
        "stop_on_timeout": _payload_bool(payload.get("stop_on_timeout")),
        "max_rounds": max(0, max_rounds),
    }


def official_rounds_operation_details(
    rounds_result: dict[str, object],
    meeting_id: str,
) -> dict[str, object]:
    results = rounds_result.get("results") if isinstance(rounds_result.get("results"), list) else []
    round_ids: list[str] = []
    statuses: list[str] = []
    role_ids: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        if item.get("round_id"):
            round_ids.append(clean_lobby_text(item.get("round_id"), limit=128))
        if item.get("status"):
            statuses.append(clean_lobby_text(item.get("status"), limit=32))
        role_ids.extend(_safe_strings(item.get("role_ids"), limit=128))
    details = {
        "meeting_id": meeting_id,
        "round_count": _nonnegative_int(rounds_result.get("round_count"), 0),
        "answered_round_count": _nonnegative_int(rounds_result.get("answered_round_count"), 0),
        "completed_round_count": _nonnegative_int(rounds_result.get("completed_round_count"), 0),
        "timeout_round_count": _nonnegative_int(rounds_result.get("timeout_round_count"), 0),
        "skipped_round_count": _nonnegative_int(rounds_result.get("skipped_round_count"), 0),
        "stopped_round_count": _nonnegative_int(rounds_result.get("stopped_round_count"), 0),
        "stopped": rounds_result.get("stopped") is True,
        "round_ids": round_ids,
        "statuses": statuses,
        "role_ids": role_ids,
        "timeout_seconds": _nonnegative_float(rounds_result.get("timeout_seconds"), 0.0),
        "max_rounds": _nonnegative_int(rounds_result.get("max_rounds"), 0),
    }
    finalization = rounds_result.get("finalization") if isinstance(rounds_result.get("finalization"), dict) else None
    if finalization is not None:
        details.update(_rounds_finalization_operation_details(finalization, meeting_id))
    return details


def official_turn_preset_request_operation_details(
    payload: dict[str, object],
    meeting_id: str,
) -> dict[str, object]:
    return {
        "meeting_id": meeting_id,
        "preset_id": clean_lobby_text(payload.get("preset_id") or payload.get("preset"), limit=128),
        "role_ids": _safe_strings(payload.get("role_ids"), limit=128),
        "timeout_seconds": _nonnegative_float(
            payload.get("timeout_seconds", payload.get("timeout")),
            30.0,
        ),
        "stop_on_timeout": _payload_bool(payload.get("stop_on_timeout")),
    }


def official_turn_preset_operation_details(
    preset_result: dict[str, object],
    meeting_id: str,
) -> dict[str, object]:
    details = turn_sequence_operation_details(preset_result, meeting_id)
    details["preset_id"] = clean_lobby_text(preset_result.get("preset_id"), limit=128)
    details["round_id"] = clean_lobby_text(preset_result.get("round_id"), limit=128)
    details["role_ids"] = _safe_strings(preset_result.get("role_ids"), limit=128)
    return details


def _rounds_finalization_operation_details(
    finalization: dict[str, object],
    meeting_id: str,
) -> dict[str, object]:
    details = {
        "finalization_status": _operation_result_status(finalization.get("status")),
        "finalization_reason": clean_lobby_text(finalization.get("reason"), limit=256),
        "finalization_meeting_id": clean_lobby_text(
            finalization.get("meeting_id") or meeting_id,
            limit=128,
        ),
        "finalization_official_event_count": _nonnegative_int(
            finalization.get("official_event_count"),
            0,
        ),
        "finalization_artifact_event_id": clean_lobby_text(
            finalization.get("artifact_event_id"),
            limit=128,
        ),
    }
    shared_memory = finalization.get("shared_memory") if isinstance(finalization.get("shared_memory"), dict) else {}
    if shared_memory:
        details.update(shared_memory_operation_details(shared_memory))
    return details


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


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _payload_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _item_count(value: object) -> int:
    return len(value) if isinstance(value, list) else 0
