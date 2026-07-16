from __future__ import annotations

from typing import Callable, Protocol

from agentsassemble.room.event_broker import ROOM_EVENT_STREAM
from agentsassemble.room.projection import (
    public_event,
    public_participant,
    public_session,
)
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text


ROOM_SNAPSHOT_EVENT_LIMIT = 200
ROOM_HISTORY_MAX_LIMIT = 200

EnsureRoom = Callable[[str], dict[str, object]]
CapabilityProjection = Callable[[dict[str, object]], dict[str, bool]]


class ProviderCatalogReader(Protocol):
    def snapshot(self, *, refresh: bool = False) -> dict[str, object]: ...


class RoomSnapshotService:
    """Build capability-projected room snapshots and bounded history pages."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        provider_catalog: ProviderCatalogReader,
        ensure_room: EnsureRoom,
        capabilities: CapabilityProjection,
    ) -> None:
        self.store = store
        self.provider_catalog = provider_catalog
        self._ensure_room = ensure_room
        self._capabilities = capabilities

    def snapshot(
        self,
        identity: dict[str, object],
        *,
        after_seq: int = 0,
    ) -> dict[str, object]:
        room_id = clean_room_text(identity.get("meeting_id"), 128)
        self._ensure_room(room_id)
        latest_seq = self.store.latest_event_sequence(room_id)
        requested_after_seq = max(0, int(after_seq or 0))
        bridge = identity.get("client_type") == "agent_bridge"
        resume_gap = False
        if bridge:
            events: list[dict[str, object]] = []
            snapshot_mode = "bridge"
        elif requested_after_seq:
            resume_gap = latest_seq - requested_after_seq > ROOM_SNAPSHOT_EVENT_LIMIT
            if resume_gap:
                events = self.store.read_events(
                    room_id,
                    limit=ROOM_SNAPSHOT_EVENT_LIMIT,
                    newest=True,
                )
                snapshot_mode = "gap"
            else:
                events = self.store.read_events(
                    room_id,
                    after_seq=requested_after_seq,
                    limit=ROOM_SNAPSHOT_EVENT_LIMIT,
                )
                snapshot_mode = "resume"
        else:
            events = self.store.read_events(
                room_id,
                limit=ROOM_SNAPSHOT_EVENT_LIMIT,
                newest=True,
            )
            snapshot_mode = "initial"
        oldest_seq = int(events[0].get("seq") or 0) if events else 0
        has_more_before = bool(
            not bridge
            and oldest_seq
            and self.store.oldest_event_sequence(room_id) < oldest_seq
        )
        stored_sessions = self.store.sessions(room_id)
        if bridge:
            own_session_id = clean_room_text(
                identity.get("session_id") or identity.get("agent_id"),
                128,
            )
            stored_sessions = [
                session
                for session in stored_sessions
                if session.get("session_id") == own_session_id
            ]
        sessions = [public_session(session) for session in stored_sessions]
        public_events = [public_event(event) for event in events]
        active_turns = [
            {
                "turn_id": session.get("active_turn_id"),
                "participant_id": session.get("participant_id"),
                "phase": session.get("turn_phase") or session.get("runtime_status"),
            }
            for session in sessions
            if session.get("active_turn_id")
        ]
        provider_catalog = {"status": "ready", "catalog_revision": "", "providers": []}
        if not bridge:
            provider_catalog = self.provider_catalog.snapshot()
        participants = self.store.participants(room_id)
        if bridge:
            participants = [
                participant
                for participant in participants
                if participant.get("participant_id") == identity.get("agent_id")
            ]
        return {
            "op": "snapshot",
            "stream": ROOM_EVENT_STREAM,
            "room": self.store.room(room_id),
            "participants": [
                public_participant(participant)
                for participant in participants
            ],
            "agent_sessions": sessions,
            "active_turns": active_turns,
            "events": public_events,
            "oldest_seq": oldest_seq,
            "last_seq": latest_seq,
            "has_more_before": has_more_before,
            "resume_gap": resume_gap,
            "snapshot_mode": snapshot_mode,
            "provider_catalog": provider_catalog,
            "available_providers": list(provider_catalog.get("providers") or []),
            "capabilities": self._capabilities(identity),
        }

    def history_page(
        self,
        room_id: str,
        *,
        before_seq: int,
        limit: int = ROOM_HISTORY_MAX_LIMIT,
    ) -> dict[str, object]:
        clean_room_id = clean_room_text(room_id, 128)
        self._ensure_room(clean_room_id)
        clean_before_seq = max(0, int(before_seq or 0))
        clean_limit = min(
            ROOM_HISTORY_MAX_LIMIT,
            max(1, int(limit or ROOM_HISTORY_MAX_LIMIT)),
        )
        events = self.store.read_events(
            clean_room_id,
            before_seq=clean_before_seq,
            limit=clean_limit,
            newest=True,
        )
        oldest_seq = int(events[0].get("seq") or 0) if events else 0
        return {
            "events": events,
            "oldest_seq": oldest_seq,
            "has_more_before": bool(
                oldest_seq
                and self.store.oldest_event_sequence(clean_room_id) < oldest_seq
            ),
            "last_seq": self.store.latest_event_sequence(clean_room_id),
        }


__all__ = [
    "CapabilityProjection",
    "EnsureRoom",
    "ProviderCatalogReader",
    "ROOM_HISTORY_MAX_LIMIT",
    "ROOM_SNAPSHOT_EVENT_LIMIT",
    "RoomSnapshotService",
]
