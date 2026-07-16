from __future__ import annotations

from typing import Callable

from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text
from agentsassemble.room.turn_coordinator import dedupe_event_ids


AttentionReset = Callable[..., dict[str, object]]
ACTIVE_RUNTIME_STATES = frozenset(
    {"starting", "idle", "busy", "paused", "recovering", "stopping"}
)


class RoomStartupSessionReconciler:
    """Reset sessions whose runtime ownership cannot survive a server restart."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        reconcile_session_attention: AttentionReset,
    ) -> None:
        self.store = store
        self._reconcile_session_attention = reconcile_session_attention

    def reconcile(self) -> None:
        for room in self.store.list_rooms(include_archived=True):
            room_id = clean_room_text(room.get("room_id"), 128)
            if not room_id:
                continue
            for session in self.store.sessions(room_id):
                if session.get("runtime_status") not in ACTIVE_RUNTIME_STATES:
                    continue
                self._reconcile_session(room_id, session)

    def _reconcile_session(
        self,
        room_id: str,
        session: dict[str, object],
    ) -> None:
        pending = dedupe_event_ids(
            [
                *list(session.get("inflight_event_ids") or []),
                *list(session.get("pending_event_ids") or []),
            ]
        )
        attention_reset = self._reconcile_session_attention(
            room_id,
            session,
            pending_event_ids=pending,
        )
        session_id = clean_room_text(session.get("session_id"), 128)
        updated = self.store.update_session_fields(
            room_id,
            session_id,
            status="unavailable",
            runtime_status="disconnected",
            pid=None,
            reported_provider_pid=None,
            bridge_pid=None,
            bridge_handle_id="",
            active_turn_id="",
            turn_phase="",
            inflight_event_ids=[],
            **attention_reset,
            recovery_required=True,
            last_error=(
                "Server restarted without a current bridge lease or owned process handle."
            ),
        )
        participant_id = clean_room_text(updated.get("participant_id"), 128)
        if participant_id and self.store.participant(room_id, participant_id):
            self.store.update_participant_fields(
                room_id,
                participant_id,
                status="detached",
            )


__all__ = [
    "ACTIVE_RUNTIME_STATES",
    "AttentionReset",
    "RoomStartupSessionReconciler",
]
