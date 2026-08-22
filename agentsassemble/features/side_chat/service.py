"""Server-memory side chat with bounded room-scoped lifetime."""
from __future__ import annotations

import threading
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Callable

from agentsassemble.features.jsonl_chat import build_chat_event
from agentsassemble.room.text import clean_room_text

SIDE_CHAT_MAX_EVENTS_PER_ROOM = 200
SIDE_CHAT_TTL = timedelta(hours=24)


def _side_chat_scope_id(value: object) -> str:
    return clean_room_text(value, limit=128)


class SideChatStore:
    """Own ephemeral human side chat for one running room server."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        max_events_per_room: int = SIDE_CHAT_MAX_EVENTS_PER_ROOM,
        ttl: timedelta = SIDE_CHAT_TTL,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_events_per_room = max(1, int(max_events_per_room))
        self._ttl = ttl
        self._events: dict[str, deque[dict[str, object]]] = {}
        self._lock = threading.RLock()

    def append(self, payload: dict[str, object]) -> dict[str, object]:
        room_id = _side_chat_scope_id(payload.get("flow_meeting_id"))
        if not room_id:
            raise ValueError("Side chat requires a room id")
        event = build_chat_event(
            {**payload, "flow_meeting_id": room_id},
            channel="side_chat",
        )
        event["created_at"] = self._clock().isoformat()
        # Side chat is intentionally independent from main-chat messages.
        event.pop("source_event_id", None)
        with self._lock:
            room_events = self._events.setdefault(room_id, deque())
            self._prune(room_events)
            room_events.append(event)
            while len(room_events) > self._max_events_per_room:
                room_events.popleft()
        return dict(event)

    def read(
        self,
        meeting_id: object,
        *,
        limit: int | None = 120,
    ) -> list[dict[str, object]]:
        room_id = _side_chat_scope_id(meeting_id)
        if not room_id or (limit is not None and limit <= 0):
            return []
        with self._lock:
            room_events = self._events.get(room_id)
            if room_events is None:
                return []
            self._prune(room_events)
            events = list(room_events)
            if not room_events:
                self._events.pop(room_id, None)
        if limit is not None:
            events = events[-limit:]
        return [dict(event) for event in events]

    def read_after(self, meeting_id: object, event_id: object) -> tuple[list[dict[str, object]], str]:
        cursor = str(event_id or "").strip()
        events = self.read(meeting_id, limit=None)
        if cursor:
            for index, event in enumerate(events):
                if event.get("id") == cursor:
                    events = events[index + 1 :]
                    break
        latest = str(events[-1].get("id") or cursor) if events else cursor
        return events, latest

    def clear_room(self, meeting_id: object) -> int:
        room_id = _side_chat_scope_id(meeting_id)
        if not room_id:
            return 0
        with self._lock:
            removed = self._events.pop(room_id, deque())
            return len(removed)

    def _prune(self, events: deque[dict[str, object]]) -> None:
        cutoff = self._clock() - self._ttl
        while events:
            try:
                created_at = datetime.fromisoformat(str(events[0].get("created_at") or ""))
            except ValueError:
                events.popleft()
                continue
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            if created_at > cutoff:
                break
            events.popleft()


__all__ = [
    "SIDE_CHAT_MAX_EVENTS_PER_ROOM",
    "SIDE_CHAT_TTL",
    "SideChatStore",
]
