from __future__ import annotations

from dataclasses import dataclass

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.room_store import RoomStore

DEFAULT_ROOM_CONTEXT_MESSAGES = 12
DEFAULT_ROOM_CONTEXT_CHARS = 4000
MAX_ROOM_CONTEXT_MESSAGES = 100


@dataclass(frozen=True)
class RoomContextWindow:
    events: tuple[dict[str, object], ...]
    text: str
    latest_event_id: str
    latest_seq: int
    total_message_count: int
    omitted_message_count: int
    filtered_internal_event_count: int
    filtered_message_delta_count: int


def project_room_context(
    store: RoomStore,
    *,
    room_id: str,
    participant_id: str,
    after_seq: int = 0,
    max_messages: int = DEFAULT_ROOM_CONTEXT_MESSAGES,
    max_chars: int = DEFAULT_ROOM_CONTEXT_CHARS,
) -> RoomContextWindow:
    """Project a bounded, room-visible message diff without scanning history."""

    clean_after_seq = max(0, int(after_seq or 0))
    clean_max_messages = min(MAX_ROOM_CONTEXT_MESSAGES, max(1, int(max_messages or DEFAULT_ROOM_CONTEXT_MESSAGES)))
    clean_max_chars = max(256, int(max_chars or DEFAULT_ROOM_CONTEXT_CHARS))
    total_messages = store.event_count(
        room_id,
        after_seq=clean_after_seq,
        event_types=("message_final",),
        exclude_actor_id=participant_id,
    )
    source_events = store.read_events(
        room_id,
        after_seq=clean_after_seq,
        limit=clean_max_messages,
        newest=True,
        event_types=("message_final",),
        exclude_actor_id=participant_id,
    )
    projected_events, lines = _project_messages(source_events, max_chars=clean_max_chars)
    omitted = max(0, total_messages - len(projected_events))
    if omitted:
        lines.insert(0, f"- [omitted {omitted} earlier room update(s)]")
    text = "\n".join(lines)
    if len(text) > clean_max_chars:
        text = text[: max(0, clean_max_chars - len(" [truncated]"))].rstrip() + " [truncated]"

    latest = source_events[-1] if source_events else {}
    delta_count = store.event_count(
        room_id,
        after_seq=clean_after_seq,
        event_types=("message_delta",),
    )
    all_message_count = store.event_count(
        room_id,
        after_seq=clean_after_seq,
        event_types=("message_final",),
    )
    visible_count = store.event_count(room_id, after_seq=clean_after_seq)
    internal_count = max(0, visible_count - all_message_count - delta_count)
    return RoomContextWindow(
        events=tuple(projected_events),
        text=text,
        latest_event_id=clean_lobby_text(latest.get("id"), limit=128),
        latest_seq=max(clean_after_seq, int(latest.get("seq") or 0)),
        total_message_count=total_messages,
        omitted_message_count=omitted,
        filtered_internal_event_count=internal_count,
        filtered_message_delta_count=delta_count,
    )


def _project_messages(
    events: list[dict[str, object]],
    *,
    max_chars: int,
) -> tuple[list[dict[str, object]], list[str]]:
    if not events:
        return [], []
    per_message_budget = max(120, (max_chars - 160) // len(events))
    projected: list[dict[str, object]] = []
    lines: list[str] = []
    for event in events:
        actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
        speaker = clean_lobby_text(
            event.get("display_name")
            or actor.get("participant_id")
            or event.get("participant_id")
            or event.get("actor_id"),
            limit=64,
        ) or "room"
        content = clean_lobby_text(event.get("content"), limit=4000)
        if not content:
            continue
        content_budget = max(80, per_message_budget - len(speaker) - 4)
        if len(content) > content_budget:
            content = content[: max(1, content_budget - len(" [truncated]"))].rstrip() + " [truncated]"
        projected_event = {
            key: event[key]
            for key in ("id", "seq", "created_at", "type", "participant_id", "actor_id", "display_name")
            if key in event and event[key] not in (None, "", [], {})
        }
        projected_event["content"] = content
        projected.append(projected_event)
        lines.append(f"- {speaker}: {content}")
    return projected, lines
