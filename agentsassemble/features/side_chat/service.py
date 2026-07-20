"""Legacy side-chat JSONL storage and meeting-scoped reads."""
from __future__ import annotations

from pathlib import Path

from agentsassemble.legacy.meeting.core.events import (
    append_side_chat_event_to_file,
    clean_lobby_text,
    read_side_chat_events,
)


def _side_chat_scope_id(value: object) -> str:
    return clean_lobby_text(value, limit=128)


def _side_chat_event_matches_meeting(event: dict[str, object], meeting_id: str) -> bool:
    if not meeting_id:
        return True
    return _side_chat_scope_id(event.get("flow_meeting_id")) == meeting_id


def _filter_side_chat_events_for_meeting(
    events: list[dict[str, object]],
    meeting_id: str | None,
) -> list[dict[str, object]]:
    scoped_meeting_id = _side_chat_scope_id(meeting_id)
    if not scoped_meeting_id:
        return events
    return [event for event in events if _side_chat_event_matches_meeting(event, scoped_meeting_id)]


def read_side_chat(
    output_root: Path,
    limit: int = 120,
    meeting_id: str | None = None,
) -> list[dict[str, object]]:
    return _filter_side_chat_events_for_meeting(
        read_side_chat_events(output_root / "side_chat.jsonl", limit=limit),
        meeting_id,
    )


def append_side_chat_event(output_root: Path, event: dict[str, object]) -> dict[str, object]:
    return append_side_chat_event_to_file(output_root / "side_chat.jsonl", event)
