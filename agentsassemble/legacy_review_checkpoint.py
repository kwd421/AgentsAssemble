"""Retained resident review-checkpoint orchestration and audit boundary."""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from agentsassemble.legacy_live_agent_diagnostics import live_agent_session_readiness_payload
from agentsassemble.legacy_meeting_operation_projection import (
    review_checkpoint_operation_details,
    review_checkpoint_request_operation_details,
)
from agentsassemble.legacy_meeting_records import read_meeting_record, safe_meeting_dir
from agentsassemble.legacy_turn_results import turn_sequence_result, turn_sequence_status
from agentsassemble.live_agent_operations import append_live_agent_operation
from agentsassemble.live_agent_processes import LiveAgentProcessSupervisor, clean_live_agent_group_id
from agentsassemble.live_agent_review_checkpoints import write_review_checkpoint_artifacts
from agentsassemble.live_agent_turns import wait_for_review_checkpoint_reply
from agentsassemble.meeting_events import append_live_event, clean_lobby_text

TurnRequester = Callable[[Path, str, dict[str, object]], dict[str, object]]


@dataclass(frozen=True)
class LegacyReviewCheckpointService:
    """Create one review checkpoint and record a prompt-free operation audit."""

    output_root: Path
    process_supervisor: LiveAgentProcessSupervisor
    turn_requester: TurnRequester

    def create(self, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
        try:
            checkpoint = create_review_checkpoint(
                self.output_root,
                self.process_supervisor,
                meeting_id,
                payload,
                turn_requester=self.turn_requester,
            )
        except ValueError as error:
            self._record(
                status="failed",
                target_id=meeting_id,
                error=str(error),
                details=review_checkpoint_request_operation_details(payload, meeting_id),
            )
            raise

        checkpoint_status = str(checkpoint.get("status") or "unknown")
        answered = checkpoint_status == "answered"
        self._record(
            status="success" if answered else "degraded",
            target_id=meeting_id,
            summary=(
                "completed resident live-agent review checkpoint"
                if answered
                else "resident live-agent review checkpoint was not fully answered"
            ),
            details=review_checkpoint_operation_details(checkpoint, meeting_id),
        )
        return checkpoint

    def record_invalid_json(self) -> None:
        self._record(status="failed", target_id="", error="Invalid JSON", details={})

    def _record(
        self,
        *,
        status: str,
        target_id: str,
        summary: str = "",
        error: str = "",
        details: dict[str, object],
    ) -> None:
        append_live_agent_operation(
            self.output_root,
            operation="review.checkpoint",
            status=status,
            target_id=target_id,
            summary=summary,
            error=error,
            details=details,
        )


def create_review_checkpoint(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    meeting_id: str,
    payload: dict[str, object],
    *,
    turn_requester: TurnRequester,
) -> dict[str, object]:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    meeting_dir = safe_meeting_dir(output_root, clean_meeting_id)
    if not clean_meeting_id or not meeting_dir.exists():
        raise ValueError(f"Meeting {clean_meeting_id or '(blank)'} was not found.")
    group_id = clean_live_agent_group_id(str(payload.get("group_id") or ""))
    if not group_id:
        raise ValueError("Live agent group id is required.")
    content = clean_lobby_text(payload.get("content") or payload.get("message"), limit=4000)
    if not content:
        raise ValueError("Review checkpoint content is required.")
    timeout_seconds = _nonnegative_float(payload.get("timeout_seconds", payload.get("timeout")), 30.0)
    checkpoint_id = clean_lobby_text(
        payload.get("checkpoint_id") or payload.get("review_checkpoint_id"),
        limit=128,
    )
    if not checkpoint_id:
        checkpoint_id = f"review-{uuid4().hex[:8]}"

    readiness = live_agent_session_readiness_payload(
        output_root,
        process_supervisor,
        meeting_id=clean_meeting_id,
        group_id=group_id,
    )
    expected_agent_ids = _expected_agent_ids(readiness)
    if readiness.get("status") != "ready":
        return _degraded_checkpoint(
            checkpoint_id=checkpoint_id,
            meeting_id=clean_meeting_id,
            group_id=group_id,
            timeout_seconds=timeout_seconds,
            expected_agent_ids=expected_agent_ids,
            readiness=readiness,
        )

    target_agent_ids = _target_agent_ids(payload.get("agent_ids"), expected_agent_ids)
    identities = _agent_identities(read_meeting_record(meeting_dir))
    results: list[dict[str, object]] = []
    for index, agent_id in enumerate(target_agent_ids):
        identity = identities.get(agent_id, {})
        request = turn_requester(
            output_root,
            clean_meeting_id,
            {
                "agent_id": agent_id,
                "role_id": clean_lobby_text(identity.get("role_id"), limit=128) or agent_id,
                "display_name": clean_lobby_text(identity.get("display_name"), limit=64) or agent_id,
                "turn_id": checkpoint_id,
                "turn_index": index,
                "content": content,
                "review_checkpoint_id": checkpoint_id,
            },
        )
        request_event = request.get("event") if isinstance(request.get("event"), dict) else {}
        source_event_id = clean_lobby_text(request_event.get("id"), limit=128)
        if not source_event_id:
            raise ValueError("Review checkpoint request could not be created.")
        wait_result = wait_for_review_checkpoint_reply(
            meeting_dir,
            agent_id=agent_id,
            source_event_id=source_event_id,
            checkpoint_id=checkpoint_id,
            timeout_seconds=timeout_seconds,
        )
        results.append(
            turn_sequence_result(
                index,
                {
                    "status": wait_result["status"],
                    "request_event": request_event,
                    "reply_event": wait_result["reply_event"],
                    "elapsed_seconds": wait_result["elapsed_seconds"],
                    "timeout_seconds": wait_result["timeout_seconds"],
                },
            )
        )

    checkpoint = _checkpoint_result(
        checkpoint_id=checkpoint_id,
        meeting_id=clean_meeting_id,
        group_id=group_id,
        timeout_seconds=timeout_seconds,
        agent_ids=target_agent_ids,
        results=results,
        readiness=readiness,
    )
    artifacts = write_review_checkpoint_artifacts(meeting_dir, checkpoint)
    checkpoint.update(artifacts)
    append_live_event(
        meeting_dir,
        {
            "kind": "artifact",
            "meeting_id": clean_meeting_id,
            "channel": "review",
            "official_record": False,
            "artifact_kind": "review_checkpoint",
            "artifact_path": artifacts["artifact_path"],
            "artifact_json_path": artifacts["artifact_json_path"],
            "review_checkpoint_id": checkpoint_id,
            "content": f"Review checkpoint artifact ready: {artifacts['artifact_path']}",
        },
    )
    return checkpoint


def _degraded_checkpoint(
    *,
    checkpoint_id: str,
    meeting_id: str,
    group_id: str,
    timeout_seconds: float,
    expected_agent_ids: list[str],
    readiness: dict[str, object],
) -> dict[str, object]:
    return {
        "status": "degraded",
        "reason": "session_not_ready",
        "checkpoint_id": checkpoint_id,
        "meeting_id": meeting_id,
        "group_id": group_id,
        "turn_count": 0,
        "answered_count": 0,
        "timeout_count": 0,
        "skipped_count": 0,
        "timeout_seconds": timeout_seconds,
        "agent_ids": [],
        "expected_agent_ids": expected_agent_ids,
        "results": [],
        "readiness": readiness,
    }


def _checkpoint_result(
    *,
    checkpoint_id: str,
    meeting_id: str,
    group_id: str,
    timeout_seconds: float,
    agent_ids: list[str],
    results: list[dict[str, object]],
    readiness: dict[str, object],
) -> dict[str, object]:
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
            turn_count=len(agent_ids),
        ),
        "checkpoint_id": checkpoint_id,
        "meeting_id": meeting_id,
        "group_id": group_id,
        "turn_count": len(agent_ids),
        "answered_count": answered_count,
        "timeout_count": timeout_count,
        "skipped_count": skipped_count,
        "cancelled_count": cancelled_count,
        "timeout_seconds": timeout_seconds,
        "agent_ids": agent_ids,
        "results": results,
        "readiness": readiness,
    }


def _expected_agent_ids(readiness: dict[str, object]) -> list[str]:
    connection = readiness.get("connection") if isinstance(readiness.get("connection"), dict) else {}
    return _strings(connection.get("agent_ids"), limit=64)


def _target_agent_ids(value: object, expected_agent_ids: list[str]) -> list[str]:
    if value is None or value == "" or value == []:
        targets = list(expected_agent_ids)
    else:
        if not isinstance(value, list):
            raise ValueError("Review checkpoint agent_ids must be an array.")
        targets = _strings(value, limit=64)
    deduped = list(dict.fromkeys(targets))
    if not deduped:
        raise ValueError("Review checkpoint requires at least one live agent.")
    expected = set(expected_agent_ids)
    unexpected = [agent_id for agent_id in deduped if agent_id not in expected]
    if unexpected:
        raise ValueError(
            "Review checkpoint target is not in the ready resident session: "
            f"{', '.join(unexpected)}."
        )
    return deduped


def _agent_identities(meeting: dict[str, object]) -> dict[str, dict[str, str]]:
    roles = {
        str(role["id"]): role
        for role in _dicts(meeting.get("roles"))
        if role.get("id")
    }
    identities: dict[str, dict[str, str]] = {}
    for binding in _dicts(meeting.get("agent_bindings")):
        agent_id = clean_lobby_text(binding.get("agent_id"), limit=64)
        role_id = clean_lobby_text(binding.get("role_id"), limit=128)
        if not agent_id:
            continue
        role = roles.get(role_id) if role_id else None
        display_name = clean_lobby_text(role.get("display_name"), limit=64) if role else ""
        identities[agent_id] = {
            "role_id": role_id or agent_id,
            "display_name": display_name or agent_id,
        }
    return identities


def _dicts(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _strings(value: object, *, limit: int) -> list[str]:
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
