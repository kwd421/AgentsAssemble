"""Retained SSE snapshot projection and framing helpers."""
from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Callable

from agentsassemble.features.side_chat.service import _filter_side_chat_events_for_meeting
from agentsassemble.legacy.live_agent.state import read_live_agents
from agentsassemble.legacy.meeting.core.events import (
    clean_lobby_text,
    read_live_events_after,
    read_lobby_events_after,
    read_side_chat_events_after,
)
from agentsassemble.legacy.meeting.queries import (
    build_meeting_stream_payload,
    project_meeting_stream_events,
)
from agentsassemble.legacy.meeting.records import safe_meeting_dir
from agentsassemble.room.members import room_members_payload
from agentsassemble.room.repository import RoomRepository


SSE_ERROR_MESSAGE_LIMIT = 500


def filter_lobby_events_for_meeting(
    events: list[dict[str, object]],
    *,
    meeting_id: str = "",
) -> list[dict[str, object]]:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    if not clean_meeting_id:
        return events
    return [
        event
        for event in events
        if clean_lobby_text(event.get("flow_meeting_id"), limit=128) == clean_meeting_id
    ]


def meeting_not_found_error(meeting_id: str) -> ValueError:
    return ValueError(f"Meeting {meeting_id} was not found.")


def sse_stream_error_payload(
    stream: str,
    error: Exception,
    meeting_id: str | None = None,
) -> dict[str, object]:
    if stream == "meeting" and meeting_id and isinstance(error, FileNotFoundError):
        message = str(meeting_not_found_error(meeting_id))
    else:
        message = str(error).replace("\r", " ").replace("\n", " ").strip()
    payload: dict[str, object] = {
        "stream": stream,
        "error": message[:SSE_ERROR_MESSAGE_LIMIT],
    }
    if meeting_id:
        payload["meeting_id"] = meeting_id
    return payload


def stream_snapshot_payload(
    output_root: Path,
    stream: str,
    meeting_id: str | None = None,
    last_event_id: str | None = None,
    *,
    repository: RoomRepository | None = None,
    sessions: list[dict[str, object]] | None = None,
    build_meeting_stream: Callable[..., dict[str, object]] = build_meeting_stream_payload,
) -> dict[str, object]:
    if stream == "lobby":
        events = read_lobby_events_after(output_root / "lobby.jsonl", last_event_id)
        events = filter_lobby_events_for_meeting(events, meeting_id=meeting_id or "")
        return {"stream": "lobby", "events": events}
    if stream == "side_chat":
        events = read_side_chat_events_after(output_root / "side_chat.jsonl", last_event_id)
        events = _filter_side_chat_events_for_meeting(events, meeting_id)
        return {"stream": "side_chat", "events": events}
    if stream == "roster":
        members_payload = room_members_payload(
            output_root,
            read_live_agents(output_root),
            meeting_id=meeting_id or "",
            sessions=list(sessions or []),
            repository=repository,
        )
        members = members_payload.get("members") or []
        return {
            "stream": "roster",
            "meeting_id": meeting_id or "",
            "members": members,
            "payload_signature": json.dumps(members, ensure_ascii=False, sort_keys=True),
        }
    if stream == "meeting":
        if not meeting_id:
            raise ValueError("Meeting id is required for meeting event stream.")
        meeting_dir = safe_meeting_dir(output_root, meeting_id)
        if not meeting_dir.exists():
            raise meeting_not_found_error(meeting_id)
        try:
            events = project_meeting_stream_events(
                read_live_events_after(meeting_dir, last_event_id)
            )
        except FileNotFoundError as error:
            raise meeting_not_found_error(meeting_id) from error
        if not meeting_dir.exists():
            raise meeting_not_found_error(meeting_id)
        payload: dict[str, object] = {
            "stream": "meeting",
            "meeting_id": meeting_id,
            "events": events,
            "payload_signature": json.dumps(events, ensure_ascii=False, sort_keys=True),
        }
        if (meeting_dir / "meeting.json").exists():
            try:
                meeting_payload = build_meeting_stream(
                    meeting_dir,
                    output_root=output_root,
                )
            except FileNotFoundError as error:
                raise meeting_not_found_error(meeting_id) from error
            except json.JSONDecodeError:
                payload["meeting_stream_snapshot_pending"] = True
                payload["payload_signature"] = json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            else:
                payload["meeting_stream_snapshot"] = meeting_payload
                payload["payload_signature"] = json.dumps(
                    meeting_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                )
        return payload
    raise ValueError(f"Unknown event stream: {stream}")


def sse_frame_id(frame: str) -> str:
    for line in frame.splitlines():
        if line.startswith("id:"):
            return line.removeprefix("id:").strip()
    return ""


def payload_signature(payload: dict[str, object]) -> str | None:
    signature = payload.get("payload_signature")
    return signature if isinstance(signature, str) and signature else None
