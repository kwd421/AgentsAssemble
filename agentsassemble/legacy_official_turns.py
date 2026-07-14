"""Retained official-turn request, call, sequence, and operation audit."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.legacy_live_agent_queries import live_events_visible_to_agent, require_live_agent
from agentsassemble.legacy_meeting_operation_projection import (
    official_turn_call_operation_details,
    official_turn_call_request_operation_details,
    official_turn_request_operation_details,
    official_turn_sequence_request_operation_details,
    turn_sequence_operation_details,
)
from agentsassemble.legacy_meeting_records import safe_meeting_dir
from agentsassemble.legacy_turn_results import turn_sequence_result, turn_sequence_status
from agentsassemble.legacy_turn_scheduler import meeting_turn_lock
from agentsassemble.live_agent_operations import append_live_agent_operation
from agentsassemble.live_agent_turns import wait_for_official_turn_reply
from agentsassemble.meeting_events import append_live_event, clean_lobby_text, read_live_events

MAX_LIVE_AGENT_SEQUENCE_TURNS = 12


@dataclass(frozen=True)
class LegacyOfficialTurnService:
    """Execute retained official-turn commands and write prompt-free audits."""

    output_root: Path

    def request(self, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
        target_agent_id = str(payload.get("agent_id") or "").strip()
        try:
            result = live_agent_turn_request_payload(self.output_root, meeting_id, payload)
        except ValueError as error:
            self._record(
                operation="official_turn.request",
                status="failed",
                target_id=target_agent_id,
                error=str(error),
                details=official_turn_request_operation_details(
                    payload,
                    meeting_id,
                    fallback_agent_id=target_agent_id,
                ),
            )
            raise
        event = result.get("event") if isinstance(result.get("event"), dict) else {}
        self._record(
            operation="official_turn.request",
            status="success",
            target_id=str(event.get("target_agent_id") or target_agent_id),
            summary="requested live-agent official turn",
            details=official_turn_request_operation_details(
                event,
                meeting_id,
                fallback_agent_id=target_agent_id,
                include_source_event=True,
            ),
        )
        return result

    def call(self, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
        target_agent_id = str(payload.get("agent_id") or "").strip()
        try:
            result = live_agent_turn_call_payload(self.output_root, meeting_id, payload)
        except ValueError as error:
            self._record(
                operation="official_turn.call",
                status="failed",
                target_id=target_agent_id,
                error=str(error),
                details=official_turn_call_request_operation_details(
                    payload,
                    meeting_id,
                    fallback_agent_id=target_agent_id,
                ),
            )
            raise
        request_event = result.get("request_event") if isinstance(result.get("request_event"), dict) else {}
        result_status = str(result.get("status") or "unknown")
        self._record(
            operation="official_turn.call",
            status="success" if result_status == "answered" else "degraded",
            target_id=str(request_event.get("target_agent_id") or target_agent_id),
            summary=(
                "completed live-agent official turn"
                if result_status == "answered"
                else "timed out waiting for live-agent official turn"
            ),
            details=official_turn_call_operation_details(
                result,
                meeting_id,
                fallback_agent_id=target_agent_id,
            ),
        )
        return result

    def sequence(self, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
        try:
            result = live_agent_turn_sequence_payload(self.output_root, meeting_id, payload)
        except ValueError as error:
            self._record(
                operation="official_turn.sequence",
                status="failed",
                target_id=meeting_id,
                error=str(error),
                details=official_turn_sequence_request_operation_details(payload, meeting_id),
            )
            raise
        answered = result.get("status") == "answered"
        self._record(
            operation="official_turn.sequence",
            status="success" if answered else "degraded",
            target_id=meeting_id,
            summary=(
                "completed live-agent official turn sequence"
                if answered
                else "live-agent official turn sequence did not fully answer"
            ),
            details=turn_sequence_operation_details(result, meeting_id),
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


def live_agent_turn_request_payload(
    output_root: Path,
    meeting_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    meeting_dir = safe_meeting_dir(output_root, clean_meeting_id)
    if not clean_meeting_id or not meeting_dir.exists():
        raise ValueError(f"Meeting {clean_meeting_id or '(blank)'} was not found.")
    with meeting_turn_lock(clean_meeting_id):
        agent_id = clean_lobby_text(payload.get("agent_id"), limit=64)
        if not agent_id:
            raise ValueError("Agent id is required.")
        agent = require_live_agent(output_root, agent_id)
        agent_meeting_id = str(agent.get("meeting_id") or "").strip()
        if agent_meeting_id != clean_meeting_id:
            raise ValueError(f"Live agent {agent_id} is not attached to meeting {clean_meeting_id}.")
        content = clean_lobby_text(payload.get("content") or payload.get("message"), limit=4000)
        if not content:
            raise ValueError("Official turn request content is required.")
        role_id = clean_lobby_text(payload.get("role_id"), limit=128) or agent_id
        display_name = (
            clean_lobby_text(payload.get("display_name"), limit=64)
            or str(agent.get("display_name") or agent_id)
        )
        event_payload: dict[str, object] = {
            "kind": "live_agent_turn_request",
            "meeting_id": clean_meeting_id,
            "actor_id": "moderator",
            "target_agent_id": agent_id,
            "role_id": role_id,
            "display_name": display_name,
            "audience": f"agent:{agent_id}",
            "content": content,
            "turn_id": clean_lobby_text(payload.get("turn_id"), limit=128),
            "turn_index": _optional_int(payload.get("turn_index")),
            "engagement_mode": "moderator_called",
        }
        review_checkpoint_id = clean_lobby_text(
            payload.get("review_checkpoint_id") or payload.get("checkpoint_id"),
            limit=128,
        )
        if review_checkpoint_id:
            event_payload.update(
                {
                    "review_checkpoint_id": review_checkpoint_id,
                    "channel": "review",
                    "official_record": False,
                }
            )
        event = append_live_event(meeting_dir, event_payload)
        return {"agent": agent, "event": event, "live_events": read_live_events(meeting_dir)}


def live_agent_turn_call_payload(
    output_root: Path,
    meeting_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    turn_request = live_agent_turn_request_payload(output_root, meeting_id, payload)
    request_event = turn_request.get("event") if isinstance(turn_request.get("event"), dict) else {}
    agent = turn_request.get("agent") if isinstance(turn_request.get("agent"), dict) else {}
    clean_meeting_id = clean_lobby_text(request_event.get("meeting_id") or meeting_id, limit=128)
    meeting_dir = safe_meeting_dir(output_root, clean_meeting_id)
    agent_id = clean_lobby_text(request_event.get("target_agent_id") or payload.get("agent_id"), limit=64)
    source_event_id = clean_lobby_text(request_event.get("id"), limit=128)
    if not agent_id or not source_event_id:
        raise ValueError("Official turn request could not be created.")
    wait_result = wait_for_official_turn_reply(
        meeting_dir,
        agent_id=agent_id,
        source_event_id=source_event_id,
        timeout_seconds=_nonnegative_float(
            payload.get("timeout_seconds", payload.get("timeout")),
            30.0,
        ),
    )
    return {
        "status": wait_result["status"],
        "agent": agent,
        "request_event": request_event,
        "reply_event": wait_result["reply_event"],
        "elapsed_seconds": wait_result["elapsed_seconds"],
        "timeout_seconds": wait_result["timeout_seconds"],
        "live_events": live_events_visible_to_agent(read_live_events(meeting_dir), agent_id),
    }


def live_agent_turn_sequence_payload(
    output_root: Path,
    meeting_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    turns = _turn_sequence(payload.get("turns"))
    clean_meeting_id = _validate_turn_sequence(output_root, meeting_id, turns)
    timeout_seconds = _nonnegative_float(payload.get("timeout_seconds", payload.get("timeout")), 30.0)
    stop_on_timeout = _payload_bool(payload.get("stop_on_timeout"))
    results: list[dict[str, object]] = []
    stopped = False
    for index, turn in enumerate(turns):
        turn_payload = dict(turn)
        turn_payload.setdefault("timeout_seconds", timeout_seconds)
        if turn_payload.get("turn_index") is None:
            turn_payload["turn_index"] = index
        result = live_agent_turn_call_payload(output_root, meeting_id, turn_payload)
        sequence_result = turn_sequence_result(index, result)
        results.append(sequence_result)
        if sequence_result["status"] != "answered" and stop_on_timeout:
            stopped = True
            results.extend(_skipped_results(turns[index + 1 :], start_index=index + 1))
            break
    answered_count = sum(1 for result in results if result["status"] == "answered")
    timeout_count = sum(1 for result in results if result["status"] == "timeout")
    skipped_count = sum(1 for result in results if result["status"] == "skipped")
    cancelled_count = sum(1 for result in results if result["status"] == "cancelled")
    return {
        "status": turn_sequence_status(
            answered_count,
            timeout_count,
            skipped_count,
            cancelled_count,
            turn_count=len(turns),
        ),
        "meeting_id": clean_meeting_id,
        "turn_count": len(turns),
        "answered_count": answered_count,
        "timeout_count": timeout_count,
        "skipped_count": skipped_count,
        "cancelled_count": cancelled_count,
        "stopped": stopped,
        "stop_on_timeout": stop_on_timeout,
        "timeout_seconds": timeout_seconds,
        "results": results,
    }


def _turn_sequence(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise ValueError("Official turn sequence requires a non-empty turns list.")
    if len(value) > MAX_LIVE_AGENT_SEQUENCE_TURNS:
        raise ValueError(f"Official turn sequence supports at most {MAX_LIVE_AGENT_SEQUENCE_TURNS} turns.")
    turns: list[dict[str, object]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"Official turn sequence item {index} must be an object.")
        turns.append(dict(item))
    return turns


def _validate_turn_sequence(
    output_root: Path,
    meeting_id: str,
    turns: list[dict[str, object]],
) -> str:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    meeting_dir = safe_meeting_dir(output_root, clean_meeting_id)
    if not clean_meeting_id or not meeting_dir.exists():
        raise ValueError(f"Meeting {clean_meeting_id or '(blank)'} was not found.")
    for index, turn in enumerate(turns):
        agent_id = clean_lobby_text(turn.get("agent_id"), limit=64)
        if not agent_id:
            raise ValueError(f"Official turn sequence item {index} requires agent_id.")
        agent = require_live_agent(output_root, agent_id)
        agent_meeting_id = str(agent.get("meeting_id") or "").strip()
        if agent_meeting_id != clean_meeting_id:
            raise ValueError(f"Live agent {agent_id} is not attached to meeting {clean_meeting_id}.")
        content = clean_lobby_text(turn.get("content") or turn.get("message"), limit=4000)
        if not content:
            raise ValueError(f"Official turn sequence item {index} requires content.")
    return clean_meeting_id


def _skipped_results(
    turns: list[dict[str, object]],
    *,
    start_index: int,
) -> list[dict[str, object]]:
    return [
        {
            "index": start_index + offset,
            "agent_id": clean_lobby_text(turn.get("agent_id"), limit=64),
            "role_id": clean_lobby_text(turn.get("role_id"), limit=128),
            "status": "skipped",
            "request_event": None,
            "reply_event": None,
            "elapsed_seconds": 0.0,
            "timeout_seconds": _nonnegative_float(
                turn.get("timeout_seconds", turn.get("timeout")),
                0.0,
            ),
        }
        for offset, turn in enumerate(turns)
    ]


def _payload_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _nonnegative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(0.0, parsed)
