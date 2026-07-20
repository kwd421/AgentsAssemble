"""Retained official-round scheduling, progress, preset, and finalization policy."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.legacy.meeting.operation_projection import (
    official_round_operation_details,
    official_round_request_operation_details,
    official_rounds_operation_details,
    official_rounds_request_operation_details,
    official_turn_preset_operation_details,
    official_turn_preset_request_operation_details,
)
from agentsassemble.legacy.meeting.records import read_meeting_record, safe_meeting_dir
from agentsassemble.legacy.meeting.official_turns import (
    MAX_LIVE_AGENT_SEQUENCE_TURNS,
    live_agent_turn_sequence_payload,
)
from agentsassemble.legacy.meeting.turn_scheduler import meeting_turn_lock
from agentsassemble.live_agent_finalization import finalize_live_agent_meeting
from agentsassemble.live_agent_operations import append_live_agent_operation
from agentsassemble.live_agent_play_presets import build_play_preset_turns
from agentsassemble.live_agent_rounds import (
    build_official_round_turns,
    completed_official_round_ids,
    remaining_official_round_ids,
)
from agentsassemble.live_agents import read_live_agents
from agentsassemble.meeting_events import clean_lobby_text, write_live_state


MAX_LIVE_AGENT_ROUND_BATCH = 8


@dataclass(frozen=True)
class LegacyOfficialRoundService:
    """Execute retained round commands and write bounded, prompt-free audits."""

    output_root: Path

    def round(self, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
        try:
            result = live_agent_turn_round_payload(self.output_root, meeting_id, payload)
        except ValueError as error:
            self._record(
                operation="official_turn.round",
                status="failed",
                target_id=meeting_id,
                error=str(error),
                details=official_round_request_operation_details(payload, meeting_id),
            )
            raise
        answered = result.get("status") in {"answered", "complete"}
        self._record(
            operation="official_turn.round",
            status="success" if answered else "degraded",
            target_id=meeting_id,
            summary=(
                "completed live-agent official round"
                if answered
                else "live-agent official round did not fully answer"
            ),
            details=official_round_operation_details(result, meeting_id),
        )
        return result

    def rounds(self, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
        try:
            result = live_agent_turn_rounds_payload(self.output_root, meeting_id, payload)
            finalization = rounds_finalization_result_if_requested(
                self.output_root,
                meeting_id,
                result,
                payload,
            )
            if finalization is not None:
                result["finalization"] = finalization
        except ValueError as error:
            self._record(
                operation="official_turn.rounds",
                status="failed",
                target_id=meeting_id,
                error=str(error),
                details=official_rounds_request_operation_details(
                    payload,
                    meeting_id,
                    max_rounds=_payload_bounded_round_count(payload.get("max_rounds")),
                ),
            )
            raise
        finalization_result = result.get("finalization") if isinstance(result.get("finalization"), dict) else None
        rounds_success = result.get("status") in {"answered", "complete"}
        finalization_success = (
            finalization_result is None
            or finalization_result.get("status") in {"finalized", "already_finalized"}
        )
        self._record(
            operation="official_turn.rounds",
            status="success" if rounds_success and finalization_success else "degraded",
            target_id=meeting_id,
            summary=(
                "completed live-agent remaining official rounds"
                if rounds_success and finalization_success
                else "completed live-agent remaining official rounds with degraded finalization"
                if rounds_success
                else "live-agent remaining official rounds did not fully answer"
            ),
            details=official_rounds_operation_details(result, meeting_id),
        )
        return result

    def preset(self, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
        try:
            result = live_agent_turn_preset_payload(self.output_root, meeting_id, payload)
        except ValueError as error:
            self._record(
                operation="official_turn.preset",
                status="failed",
                target_id=meeting_id,
                error=str(error),
                details=official_turn_preset_request_operation_details(payload, meeting_id),
            )
            raise
        answered = result.get("status") == "answered"
        self._record(
            operation="official_turn.preset",
            status="success" if answered else "degraded",
            target_id=meeting_id,
            summary=(
                "completed live-agent play preset"
                if answered
                else "live-agent play preset did not fully answer"
            ),
            details=official_turn_preset_operation_details(result, meeting_id),
        )
        return result

    def record_invalid_json(self, operation: str) -> None:
        self._record(
            operation=operation,
            status="failed",
            target_id="",
            error="Invalid JSON",
            details={},
        )

    def _record(
        self,
        *,
        operation: str,
        status: str,
        target_id: str,
        summary: str = "",
        error: str = "",
        details: dict[str, object],
    ) -> None:
        append_live_agent_operation(
            self.output_root,
            operation=operation,
            status=status,
            target_id=target_id,
            summary=summary,
            error=error,
            details=details,
        )


def live_agent_turn_preset_payload(
    output_root: Path,
    meeting_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    clean_meeting_id, meeting_dir = _existing_meeting(output_root, meeting_id)
    preset_turns = build_play_preset_turns(
        read_meeting_record(meeting_dir),
        read_live_agents(output_root),
        meeting_id=clean_meeting_id,
        preset_id=str(payload.get("preset_id") or payload.get("preset") or ""),
        role_ids=_payload_role_ids(payload.get("role_ids")),
    )
    sequence = live_agent_turn_sequence_payload(
        output_root,
        clean_meeting_id,
        {
            "turns": preset_turns["turns"],
            "timeout_seconds": _nonnegative_float(
                payload.get("timeout_seconds", payload.get("timeout")),
                30.0,
            ),
            "stop_on_timeout": _payload_bool(payload.get("stop_on_timeout")),
        },
    )
    return {
        **sequence,
        "preset_id": preset_turns["preset_id"],
        "label": preset_turns["label"],
        "round_id": preset_turns["round_id"],
        "role_ids": preset_turns["role_ids"],
    }


def live_agent_turn_round_payload(
    output_root: Path,
    meeting_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    clean_meeting_id, meeting_dir = _existing_meeting(output_root, meeting_id)
    with meeting_turn_lock(clean_meeting_id):
        return _live_agent_turn_round_payload_locked(
            output_root,
            clean_meeting_id,
            meeting_dir,
            payload,
        )


def _live_agent_turn_round_payload_locked(
    output_root: Path,
    clean_meeting_id: str,
    meeting_dir: Path,
    payload: dict[str, object],
) -> dict[str, object]:
    meeting = read_meeting_record(meeting_dir)
    round_id = clean_lobby_text(payload.get("round_id"), limit=128)
    if round_id in completed_official_round_ids(meeting):
        return _completed_official_round_result(clean_meeting_id, round_id)
    round_turns = build_official_round_turns(
        meeting,
        read_live_agents(output_root),
        meeting_id=clean_meeting_id,
        round_id=round_id,
        instruction=payload.get("content") or payload.get("instruction") or payload.get("message"),
        role_ids=_payload_role_ids(payload.get("role_ids")),
        max_turns=MAX_LIVE_AGENT_SEQUENCE_TURNS,
    )
    sequence = live_agent_turn_sequence_payload(
        output_root,
        clean_meeting_id,
        {
            "turns": round_turns["turns"],
            "timeout_seconds": _nonnegative_float(
                payload.get("timeout_seconds", payload.get("timeout")),
                30.0,
            ),
            "stop_on_timeout": _payload_bool(payload.get("stop_on_timeout")),
        },
    )
    result = dict(sequence)
    result["round_id"] = round_turns["round_id"]
    result["role_ids"] = round_turns["role_ids"]
    _record_answered_official_round_progress(meeting_dir, result)
    return result


def live_agent_turn_rounds_payload(
    output_root: Path,
    meeting_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    clean_meeting_id, meeting_dir = _existing_meeting(output_root, meeting_id)
    max_rounds = _payload_bounded_round_count(payload.get("max_rounds"))
    timeout_seconds = _nonnegative_float(
        payload.get("timeout_seconds", payload.get("timeout")),
        30.0,
    )
    stop_on_timeout = _payload_bool(payload.get("stop_on_timeout"))
    with meeting_turn_lock(clean_meeting_id):
        meeting = read_meeting_record(meeting_dir)
        round_ids = remaining_official_round_ids(meeting, max_rounds=max_rounds)
        return _live_agent_turn_rounds_payload_locked(
            output_root,
            clean_meeting_id,
            round_ids,
            timeout_seconds=timeout_seconds,
            stop_on_timeout=stop_on_timeout,
            max_rounds=max_rounds,
        )


def _live_agent_turn_rounds_payload_locked(
    output_root: Path,
    clean_meeting_id: str,
    round_ids: list[str],
    *,
    timeout_seconds: float,
    stop_on_timeout: bool,
    max_rounds: int,
) -> dict[str, object]:
    results: list[dict[str, object]] = []
    stopped = False
    for index, round_id in enumerate(round_ids):
        if stopped:
            results.append(_skipped_round_result(index, round_id, timeout_seconds))
            continue
        round_result = live_agent_turn_round_payload(
            output_root,
            clean_meeting_id,
            {
                "round_id": round_id,
                "timeout_seconds": timeout_seconds,
                "stop_on_timeout": stop_on_timeout,
            },
        )
        summary = _live_agent_round_batch_result(index, round_result)
        results.append(summary)
        if summary["status"] != "answered" and stop_on_timeout:
            stopped = True
    answered_count = sum(1 for result in results if result["status"] == "answered")
    completed_count = sum(1 for result in results if result["status"] == "complete")
    timeout_count = sum(1 for result in results if result["status"] == "timeout")
    skipped_count = sum(1 for result in results if result["status"] == "skipped")
    stopped_count = sum(1 for result in results if result["status"] == "stopped")
    return {
        "status": _live_agent_round_batch_status(
            answered_count,
            completed_count,
            timeout_count,
            skipped_count,
            stopped_count,
            len(results),
        ),
        "meeting_id": clean_meeting_id,
        "round_count": len(results),
        "answered_round_count": answered_count,
        "completed_round_count": completed_count,
        "timeout_round_count": timeout_count,
        "skipped_round_count": skipped_count,
        "stopped_round_count": stopped_count,
        "stopped": stopped,
        "stop_on_timeout": stop_on_timeout,
        "timeout_seconds": timeout_seconds,
        "max_rounds": max_rounds,
        "results": results,
    }


def rounds_finalization_result_if_requested(
    output_root: Path,
    meeting_id: str,
    rounds_result: dict[str, object],
    payload: dict[str, object],
) -> dict[str, object] | None:
    if not _payload_bool(payload.get("finalize_after_rounds")):
        return None
    clean_meeting_id = clean_lobby_text(rounds_result.get("meeting_id") or meeting_id, limit=128)
    if _result_status(rounds_result.get("status")) not in {"answered", "complete"}:
        return skipped_rounds_finalization_result(clean_meeting_id, reason="rounds_not_ready")
    try:
        meeting_dir = safe_meeting_dir(output_root, clean_meeting_id)
        meeting = read_meeting_record(meeting_dir)
    except ValueError as error:
        reason = clean_lobby_text(str(error), limit=256) or "finalization_failed"
        return _failed_rounds_finalization_result(clean_meeting_id, reason=reason)
    except (OSError, json.JSONDecodeError):
        return _failed_rounds_finalization_result(clean_meeting_id, reason="finalization_failed")
    if remaining_official_round_ids(meeting, max_rounds=None):
        return skipped_rounds_finalization_result(clean_meeting_id, reason="rounds_still_remaining")
    try:
        return finalize_live_agent_meeting(meeting_dir)
    except ValueError as error:
        reason = clean_lobby_text(str(error), limit=256) or "finalization_failed"
        return _failed_rounds_finalization_result(clean_meeting_id, reason=reason)
    except (OSError, json.JSONDecodeError):
        return _failed_rounds_finalization_result(clean_meeting_id, reason="finalization_failed")


def skipped_rounds_finalization_result(meeting_id: str, *, reason: str) -> dict[str, object]:
    return {
        "status": "skipped",
        "reason": clean_lobby_text(reason, limit=128),
        "meeting_id": clean_lobby_text(meeting_id, limit=128),
        "official_event_count": 0,
    }


def _failed_rounds_finalization_result(meeting_id: str, *, reason: str) -> dict[str, object]:
    return {
        "status": "failed",
        "reason": clean_lobby_text(reason, limit=256) or "finalization_failed",
        "meeting_id": clean_lobby_text(meeting_id, limit=128),
        "official_event_count": 0,
    }


def _existing_meeting(output_root: Path, meeting_id: str) -> tuple[str, Path]:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    meeting_dir = safe_meeting_dir(output_root, clean_meeting_id)
    if not clean_meeting_id or not meeting_dir.exists():
        raise ValueError(f"Meeting {clean_meeting_id or '(blank)'} was not found.")
    return clean_meeting_id, meeting_dir


def _record_answered_official_round_progress(
    meeting_dir: Path,
    round_result: dict[str, object],
) -> None:
    if round_result.get("status") != "answered":
        return
    round_id = clean_lobby_text(round_result.get("round_id"), limit=128)
    if not round_id:
        return
    meeting = read_meeting_record(meeting_dir)
    progress = {
        "id": round_id,
        "status": "answered",
        "role_ids": _safe_role_ids(round_result.get("role_ids")),
        "turn_count": _nonnegative_int(round_result.get("turn_count"), 0),
        "answered_count": _nonnegative_int(round_result.get("answered_count"), 0),
        "timeout_count": _nonnegative_int(round_result.get("timeout_count"), 0),
        "skipped_count": _nonnegative_int(round_result.get("skipped_count"), 0),
    }
    updated_rounds: list[dict[str, object]] = []
    replaced = False
    for item in _dict_items(meeting.get("debate_rounds")):
        item_round_id = clean_lobby_text(item.get("id") or item.get("round"), limit=128)
        if item_round_id == round_id:
            if not replaced:
                merged = dict(item)
                merged.update(progress)
                updated_rounds.append(merged)
                replaced = True
            continue
        updated_rounds.append(item)
    if not replaced:
        updated_rounds.append(progress)
    meeting["debate_rounds"] = updated_rounds
    write_live_state(meeting_dir, meeting)


def _completed_official_round_result(meeting_id: str, round_id: str) -> dict[str, object]:
    return {
        "status": "complete",
        "meeting_id": meeting_id,
        "round_id": round_id,
        "role_ids": [],
        "turn_count": 0,
        "answered_count": 0,
        "timeout_count": 0,
        "skipped_count": 0,
        "stopped": False,
        "stop_on_timeout": False,
        "timeout_seconds": 0.0,
        "results": [],
    }


def _payload_bounded_round_count(value: object) -> int:
    requested = _nonnegative_int(value, MAX_LIVE_AGENT_ROUND_BATCH)
    if requested <= 0:
        return MAX_LIVE_AGENT_ROUND_BATCH
    return min(requested, MAX_LIVE_AGENT_ROUND_BATCH)


def _live_agent_round_batch_result(
    index: int,
    round_result: dict[str, object],
) -> dict[str, object]:
    return {
        "index": index,
        "round_id": clean_lobby_text(round_result.get("round_id"), limit=128),
        "status": str(round_result.get("status") or "unknown"),
        "role_ids": _safe_role_ids(round_result.get("role_ids")),
        "turn_count": _nonnegative_int(round_result.get("turn_count"), 0),
        "answered_count": _nonnegative_int(round_result.get("answered_count"), 0),
        "timeout_count": _nonnegative_int(round_result.get("timeout_count"), 0),
        "skipped_count": _nonnegative_int(round_result.get("skipped_count"), 0),
    }


def _skipped_round_result(index: int, round_id: str, timeout_seconds: float) -> dict[str, object]:
    return {
        "index": index,
        "round_id": clean_lobby_text(round_id, limit=128),
        "status": "skipped",
        "role_ids": [],
        "turn_count": 0,
        "answered_count": 0,
        "timeout_count": 0,
        "skipped_count": 0,
        "timeout_seconds": timeout_seconds,
    }


def _live_agent_round_batch_status(
    answered_count: int,
    completed_count: int,
    timeout_count: int,
    skipped_count: int,
    stopped_count: int,
    round_count: int,
) -> str:
    if round_count == 0:
        return "complete"
    if answered_count == round_count:
        return "answered"
    if answered_count + completed_count == round_count:
        return "answered" if answered_count else "complete"
    if stopped_count or skipped_count:
        return "stopped"
    if timeout_count:
        return "timeout"
    return "degraded"


def _payload_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _nonnegative_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (OverflowError, TypeError, ValueError):
        return default
    return max(0, parsed)


def _nonnegative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(0.0, parsed)


def _payload_role_ids(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Official round role_ids must be an array.")
    role_ids: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"Official round role_ids item {index} must be a string.")
        role_ids.append(item)
    return role_ids


def _safe_role_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        cleaned
        for item in value
        if isinstance(item, str) and (cleaned := clean_lobby_text(item, limit=128))
    ]


def _dict_items(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _result_status(value: object) -> str:
    return str(value or "unknown").strip() or "unknown"
