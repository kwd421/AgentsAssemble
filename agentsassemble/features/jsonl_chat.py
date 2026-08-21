"""Small bounded JSONL chat store used by optional room surfaces."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agentsassemble.room.text import clean_room_text


def append_chat_event(
    path: Path,
    payload: dict[str, object],
    *,
    channel: str,
) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = build_chat_event(payload, channel=channel)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event


def build_chat_event(
    payload: dict[str, object],
    *,
    channel: str,
) -> dict[str, object]:
    """Normalize one chat event without choosing its storage lifetime."""

    return {
        "id": uuid4().hex[:12],
        "created_at": datetime.now(UTC).isoformat(),
        "name": clean_room_text(payload.get("name"), limit=32) or "guest",
        "side": _side(payload.get("side")),
        "kind": "message",
        "message": clean_room_text(payload.get("message"), limit=2000),
        "channel": clean_room_text(channel, limit=64),
        "audience": "room",
        "official_record": False,
        "actor_id": clean_room_text(payload.get("actor_id"), limit=128),
        "actor_type": clean_room_text(payload.get("actor_type"), limit=32),
        "target_agent_id": clean_room_text(payload.get("target_agent_id"), limit=128),
        "source_event_id": clean_room_text(payload.get("source_event_id"), limit=128),
        "flow_meeting_id": clean_room_text(payload.get("flow_meeting_id"), limit=128),
        "attachments": _attachments(payload.get("attachments")),
    }


def read_chat_events(path: Path, *, limit: int = 120) -> list[dict[str, object]]:
    if not path.exists() or limit <= 0:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    events: list[dict[str, object]] = []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def read_chat_events_after(
    path: Path,
    event_id: str,
    *,
    limit: int = 120,
) -> list[dict[str, object]]:
    events = read_chat_events(path, limit=limit)
    if not event_id:
        return events
    for index, event in enumerate(events):
        if event.get("id") == event_id:
            return events[index + 1 :]
    return events


def _side(value: object) -> str:
    side = clean_room_text(value, limit=32)
    return side if side in {"mine", "my-agent", "other", "other-agent"} else "other"


def _attachments(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value[:8] if isinstance(item, dict)]


__all__ = [
    "append_chat_event",
    "build_chat_event",
    "read_chat_events",
    "read_chat_events_after",
]
