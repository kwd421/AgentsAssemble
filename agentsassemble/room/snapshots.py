from __future__ import annotations

from typing import Callable, Protocol

from agentsassemble.room.event_broker import ROOM_EVENT_STREAM
from agentsassemble.room.projection import (
    public_event_for_identity,
    public_participant,
    public_session,
)
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.global_settings import public_room_global_settings
from agentsassemble.room.text import clean_room_text


ROOM_SNAPSHOT_EVENT_LIMIT = 200
ROOM_HISTORY_MAX_LIMIT = 200
BRIDGE_ROOM_VIEW_MESSAGE_LIMIT = 50
BRIDGE_ROOM_VIEW_CHAR_LIMIT = 32_768
_PUBLIC_PROVIDER_REQUEST_FIELDS = frozenset(
    {
        "provider_request_id",
        "request_kind",
        "response_kind",
        "title",
        "description",
        "status",
        "options",
        "questions",
        "timeout_seconds",
        "action_url",
    }
)

EnsureRoom = Callable[[str], dict[str, object]]
CapabilityProjection = Callable[[dict[str, object]], dict[str, bool]]


class ProviderCatalogReader(Protocol):
    def current_snapshot(self) -> dict[str, object]: ...


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
            events = self._bridge_room_view_events(room_id)
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
        capabilities = self._capabilities(identity)
        provider_requests = [] if bridge else _pending_provider_requests(
            stored_sessions,
            identity,
        )
        sessions = [public_session(session) for session in stored_sessions]
        public_events = [public_event_for_identity(event, identity) for event in events]
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
            provider_catalog = self.provider_catalog.current_snapshot()
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
            "room_settings": public_room_global_settings(
                self.store.room_settings(room_id)
            ),
            "participants": [
                public_participant(participant)
                for participant in participants
            ],
            "agent_sessions": sessions,
            "provider_requests": provider_requests,
            "active_turns": active_turns,
            "events": public_events,
            "oldest_seq": oldest_seq,
            "last_seq": latest_seq,
            "has_more_before": has_more_before,
            "resume_gap": resume_gap,
            "snapshot_mode": snapshot_mode,
            "provider_catalog": provider_catalog,
            "available_providers": list(provider_catalog.get("providers") or []),
            "capabilities": capabilities,
        }

    def _bridge_room_view_events(self, room_id: str) -> list[dict[str, object]]:
        candidates = self.store.read_events(
            room_id,
            event_types=("message_final",),
            limit=BRIDGE_ROOM_VIEW_MESSAGE_LIMIT,
            newest=True,
        )
        selected: list[dict[str, object]] = []
        used_chars = 0
        for event in reversed(candidates):
            content_chars = len(str(event.get("content") or ""))
            attachment_chars = sum(
                len(str(item.get("filename") or "")) + len(str(item.get("content_type") or ""))
                for item in (
                    event.get("attachments")
                    if isinstance(event.get("attachments"), list)
                    else []
                )
                if isinstance(item, dict)
            )
            event_chars = content_chars + attachment_chars
            if selected and used_chars + event_chars > BRIDGE_ROOM_VIEW_CHAR_LIMIT:
                break
            selected.append(event)
            used_chars += event_chars
        selected.reverse()
        return selected

    def history_page(
        self,
        room_id: str,
        *,
        identity: dict[str, object],
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
            "events": [public_event_for_identity(event, identity) for event in events],
            "oldest_seq": oldest_seq,
            "has_more_before": bool(
                oldest_seq
                and self.store.oldest_event_sequence(clean_room_id) < oldest_seq
            ),
            "last_seq": self.store.latest_event_sequence(clean_room_id),
        }


def _pending_provider_requests(
    sessions: list[dict[str, object]],
    identity: dict[str, object],
) -> list[dict[str, object]]:
    principals = {
        clean_room_text(identity.get("agent_id"), 128),
        clean_room_text(identity.get("user_id"), 128),
        clean_room_text(identity.get("session_id"), 128),
    }
    principals.discard("")
    visible: list[dict[str, object]] = []
    for session in sessions:
        pending = session.get("pending_provider_request")
        if not isinstance(pending, dict) or not pending:
            continue
        owner_id = clean_room_text(pending.get("owner_id"), 128)
        if owner_id not in principals:
            continue
        request = {
            key: value
            for key, value in pending.items()
            if key in _PUBLIC_PROVIDER_REQUEST_FIELDS
        }
        request.update(
            participant_id=clean_room_text(session.get("participant_id"), 128),
            display_name=clean_room_text(session.get("display_name"), 160),
            provider_kind=clean_room_text(session.get("provider_kind"), 128),
        )
        visible.append(request)
    return visible


__all__ = [
    "CapabilityProjection",
    "EnsureRoom",
    "ProviderCatalogReader",
    "ROOM_HISTORY_MAX_LIMIT",
    "ROOM_SNAPSHOT_EVENT_LIMIT",
    "BRIDGE_ROOM_VIEW_CHAR_LIMIT",
    "BRIDGE_ROOM_VIEW_MESSAGE_LIMIT",
    "RoomSnapshotService",
]
