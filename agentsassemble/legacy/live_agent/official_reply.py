"""Retained resident official/review reply recording and audit."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentsassemble.legacy.live_agent.queries import live_events_visible_to_agent, require_live_agent
from agentsassemble.legacy_meeting_operation_projection import (
    official_reply_operation_details,
    official_reply_request_operation_details,
    shared_memory_operation_details,
)
from agentsassemble.legacy_meeting_records import read_meeting_record, safe_meeting_dir
from agentsassemble.live_agent_operations import append_live_agent_operation
from agentsassemble.live_agent_turns import (
    is_official_turn_reply_event,
    is_review_checkpoint_reply_event,
    official_turn_cancellation,
)
from agentsassemble.live_agents import heartbeat_live_agent
from agentsassemble.live_meeting_memory import write_live_meeting_memory_artifacts
from agentsassemble.meeting_events import (
    append_live_event,
    clean_lobby_text,
    read_live_events,
    write_live_state,
)
from agentsassemble.room.speech import ActorIdentity, governed_official_reply


OFFICIAL_REPLY_LOCK = threading.Lock()


@dataclass(frozen=True)
class LegacyLiveAgentOfficialReplyService:
    output_root: Path

    def reply(self, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
        try:
            result = live_agent_official_turn_payload(self.output_root, agent_id, payload)
        except ValueError as error:
            self._record(
                operation="official_turn.reply",
                status="failed",
                agent_id=agent_id,
                error=str(error),
                details=official_reply_request_operation_details(payload),
            )
            raise

        event = result.get("event") if isinstance(result.get("event"), dict) else {}
        review_checkpoint_id = clean_lobby_text(event.get("review_checkpoint_id"), limit=128)
        operation = "review.reply" if review_checkpoint_id else "official_turn.reply"
        shared_memory = result.get("shared_memory") if isinstance(result.get("shared_memory"), dict) else {}
        self._record(
            operation=operation,
            status="success",
            agent_id=agent_id,
            summary=(
                "recorded live-agent review checkpoint reply"
                if review_checkpoint_id
                else "recorded live-agent official turn"
            ),
            details=official_reply_operation_details(event, payload, shared_memory),
        )
        return result

    def _record(
        self,
        *,
        operation: str,
        status: str,
        agent_id: str,
        summary: str = "",
        error: str = "",
        details: dict[str, object],
    ) -> None:
        append_live_agent_operation(
            self.output_root,
            operation=operation,
            status=status,
            target_id=agent_id,
            summary=summary,
            error=error,
            details=details,
        )


def live_agent_official_turn_payload(
    output_root: Path,
    agent_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    agent = require_live_agent(output_root, agent_id)
    meeting_id = clean_lobby_text(payload.get("meeting_id") or agent.get("meeting_id"), limit=128)
    agent_meeting_id = clean_lobby_text(agent.get("meeting_id"), limit=128)
    if not agent_meeting_id or meeting_id != agent_meeting_id:
        raise ValueError(f"Live agent {agent_id} is not attached to meeting {meeting_id or '(blank)'}.")
    meeting_dir = safe_meeting_dir(output_root, meeting_id)
    if not meeting_id or not meeting_dir.exists():
        raise ValueError(f"Meeting {meeting_id or '(blank)'} was not found.")
    content = clean_lobby_text(payload.get("content") or payload.get("message"), limit=4000)
    if not content:
        raise ValueError("Official turn content is required.")
    source_event_id = clean_lobby_text(payload.get("source_event_id"), limit=128)
    if not source_event_id:
        raise ValueError("Official turn source_event_id is required.")

    with OFFICIAL_REPLY_LOCK:
        request_event = matching_live_agent_turn_request(meeting_dir, agent_id, source_event_id)
        if request_event is None:
            raise ValueError("Matching official turn request was not found.")
        if official_turn_cancellation(
            read_live_events(meeting_dir, limit=None),
            agent_id=agent_id,
            source_event_id=source_event_id,
        ):
            raise ValueError("Official turn request was cancelled.")
        event = live_agent_reply_for_request(
            meeting_dir,
            agent_id,
            source_event_id,
            request_event,
        )
        if event is None:
            event = _append_official_reply(
                meeting_dir,
                agent=agent,
                agent_id=agent_id,
                meeting_id=meeting_id,
                source_event_id=source_event_id,
                request_event=request_event,
                content=content,
            )
        shared_memory = refresh_live_meeting_memory_after_official_reply(meeting_dir, event)

    updated_agent = heartbeat_live_agent(
        output_root,
        agent_id,
        status="online",
        metadata={
            "last_error": "",
            "last_reply_at": datetime.now(UTC).isoformat(),
            "last_observed_live_event_id": source_event_id,
        },
    )
    return {
        "agent": updated_agent,
        "event": event,
        "shared_memory": shared_memory,
        "live_events": live_events_visible_to_agent(read_live_events(meeting_dir), agent_id),
    }


def _append_official_reply(
    meeting_dir: Path,
    *,
    agent: dict[str, object],
    agent_id: str,
    meeting_id: str,
    source_event_id: str,
    request_event: dict[str, object],
    content: str,
) -> dict[str, object]:
    role_id = clean_lobby_text(request_event.get("role_id"), limit=128) or agent_id
    display_name = (
        clean_lobby_text(request_event.get("display_name"), limit=64)
        or clean_lobby_text(agent.get("display_name"), limit=64)
        or agent_id
    )
    request_turn_index = request_event.get("turn_index")
    turn_index = (
        request_turn_index
        if isinstance(request_turn_index, int) and not isinstance(request_turn_index, bool)
        else None
    )
    return governed_official_reply(
        meeting_dir,
        identity=ActorIdentity(
            agent_id=agent_id,
            display_name=display_name,
            participant_type="live_session",
            meeting_id=meeting_id,
        ),
        meeting_id=meeting_id,
        source_event_id=source_event_id,
        role_id=role_id,
        display_name=display_name,
        content=content,
        turn_id=clean_lobby_text(request_event.get("turn_id"), limit=128),
        turn_index=turn_index,
        review_checkpoint_id=clean_lobby_text(request_event.get("review_checkpoint_id"), limit=128),
        append_live_event=append_live_event,
    )


def refresh_live_meeting_memory_after_official_reply(
    meeting_dir: Path,
    event: dict[str, object],
) -> dict[str, object]:
    if event.get("official_record") is not True or event.get("channel") != "official":
        return {}
    try:
        meeting = read_meeting_record(meeting_dir)
        can_update_live_state = True
    except (ValueError, OSError, json.JSONDecodeError):
        meeting = {
            "meeting_id": clean_lobby_text(event.get("meeting_id"), limit=128),
            "topic": clean_lobby_text(event.get("meeting_id"), limit=240),
        }
        can_update_live_state = False
    memory = write_live_meeting_memory_artifacts(meeting_dir, meeting=meeting)
    if can_update_live_state:
        meeting["shared_memory"] = memory
        write_live_state(meeting_dir, meeting)
    return shared_memory_operation_details(memory)


def matching_live_agent_turn_request(
    meeting_dir: Path,
    agent_id: str,
    source_event_id: str,
) -> dict[str, object] | None:
    for event in read_live_events(meeting_dir, limit=None):
        if event.get("id") != source_event_id:
            continue
        if event.get("kind") != "live_agent_turn_request":
            return None
        if str(event.get("target_agent_id") or "") != agent_id:
            return None
        return event
    return None


def live_agent_reply_for_request(
    meeting_dir: Path,
    agent_id: str,
    source_event_id: str,
    request_event: dict[str, object],
) -> dict[str, object] | None:
    checkpoint_id = clean_lobby_text(request_event.get("review_checkpoint_id"), limit=128)
    for event in read_live_events(meeting_dir, limit=None):
        if checkpoint_id:
            if not is_review_checkpoint_reply_event(event):
                continue
            if clean_lobby_text(event.get("review_checkpoint_id"), limit=128) != checkpoint_id:
                continue
        elif not is_official_turn_reply_event(event):
            continue
        if str(event.get("actor_id") or "") != agent_id:
            continue
        if str(event.get("source_event_id") or "") != source_event_id:
            continue
        return event
    return None
