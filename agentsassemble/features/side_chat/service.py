"""Side-chat JSONL storage and room-scoped reads."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from uuid import uuid4

from agentsassemble.features.jsonl_chat import append_chat_event, read_chat_events
from agentsassemble.room.text import clean_room_text

_SIDE_CHAT_WRITE_LOCK = threading.Lock()


def _side_chat_scope_id(value: object) -> str:
    return clean_room_text(value, limit=128)


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
    limit: int | None = 120,
    meeting_id: str | None = None,
) -> list[dict[str, object]]:
    path = output_root / "side_chat.jsonl"
    scoped_meeting_id = _side_chat_scope_id(meeting_id)
    if not scoped_meeting_id:
        return read_chat_events(path, limit=limit or 120)
    if limit is not None and limit <= 0:
        return []
    return _filter_side_chat_events_for_meeting(
        read_chat_events(path, limit=max(limit or 120, 120)),
        scoped_meeting_id,
    )[-limit:] if limit is not None else _filter_side_chat_events_for_meeting(
        read_chat_events(path, limit=10000), scoped_meeting_id
    )


def append_side_chat_event(output_root: Path, event: dict[str, object]) -> dict[str, object]:
    with _SIDE_CHAT_WRITE_LOCK:
        return append_chat_event(
            output_root / "side_chat.jsonl",
            event,
            channel="side_chat",
        )


def delete_room_side_chat_events(output_root: Path, meeting_id: str) -> int:
    """Remove one room's side-chat rows without disturbing other rooms."""
    scoped_meeting_id = _side_chat_scope_id(meeting_id)
    if not scoped_meeting_id:
        return 0
    path = output_root / "side_chat.jsonl"
    with _SIDE_CHAT_WRITE_LOCK:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return 0
        retained: list[str] = []
        removed = 0
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                retained.append(line)
                continue
            if (
                isinstance(event, dict)
                and _side_chat_event_matches_meeting(event, scoped_meeting_id)
            ):
                removed += 1
            else:
                retained.append(line)
        if removed == 0:
            return 0
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                "".join(f"{line}\n" for line in retained),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return removed
