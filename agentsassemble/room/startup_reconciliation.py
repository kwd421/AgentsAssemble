from __future__ import annotations

import logging
from contextlib import AbstractContextManager
from typing import Callable

from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.projection import public_session
from agentsassemble.room.provider_requests import fail_pending_provider_request
from agentsassemble.room.text import clean_room_text
from agentsassemble.room.turn_coordinator import dedupe_event_ids


AttentionReset = Callable[..., dict[str, object]]
SessionStarter = Callable[..., dict[str, object]]
ACTIVE_RUNTIME_STATES = frozenset(
    {"starting", "idle", "busy", "paused", "recovering", "stopping"}
)
_LOGGER = logging.getLogger(__name__)


class RoomStartupSessionReconciler:
    """Reset sessions whose runtime ownership cannot survive a server restart."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        reconcile_session_attention: AttentionReset,
        lock: AbstractContextManager[object] | None = None,
        start_session: SessionStarter | None = None,
    ) -> None:
        self.store = store
        self._reconcile_session_attention = reconcile_session_attention
        self._lock = lock
        self._start_session = start_session

    def reconcile(self) -> None:
        for room in self.store.list_rooms(include_archived=True):
            room_id = clean_room_text(room.get("room_id"), 128)
            if not room_id or room.get("status") == "closed":
                continue
            for session in self.store.sessions(room_id):
                fail_pending_provider_request(
                    self.store,
                    room_id,
                    clean_room_text(session.get("session_id"), 128),
                    reason_code="provider_request_server_restarted",
                )
                if session.get("runtime_status") not in ACTIVE_RUNTIME_STATES:
                    continue
                self.disconnect_session(room_id, session)

    def disconnect_session(
        self,
        room_id: str,
        session: dict[str, object],
    ) -> dict[str, object]:
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
        return updated

    def restart_preserved_server_sessions(
        self,
        *,
        server_url: str,
        ticket_issuer: Callable[[dict[str, object]], object],
    ) -> list[dict[str, object]]:
        """Replace server-owned bridges that crossed a rolling process boundary."""

        if self._lock is None or self._start_session is None:
            raise RuntimeError("Rolling session replacement is not configured.")
        candidates: list[tuple[str, str]] = []
        with self._lock:
            for room in self.store.list_rooms(include_archived=True):
                room_id = clean_room_text(room.get("room_id"), 128)
                if not room_id or room.get("status") == "closed":
                    continue
                for session in self.store.sessions(room_id):
                    session_id = clean_room_text(session.get("session_id"), 128)
                    if (
                        session_id
                        and session.get("process_ownership") == "server"
                        and session.get("enabled")
                        and session.get("runtime_status") in ACTIVE_RUNTIME_STATES
                    ):
                        self.disconnect_session(room_id, session)
                        candidates.append((room_id, session_id))
        restarted: list[dict[str, object]] = []
        for room_id, session_id in candidates:
            try:
                restarted.append(
                    self._start_session(
                        room_id,
                        session_id,
                        server_url=server_url,
                        ticket_issuer=ticket_issuer,
                        automatic_recovery=True,
                    )
                )
            except RoomCommandRejected:
                current = self.store.session(room_id, session_id)
                _LOGGER.error(
                    "Rolling replacement failed to relaunch Agent Session %s in %s: %s",
                    session_id,
                    room_id,
                    current.get("last_error") or "provider relaunch failed",
                )
                restarted.append(
                    {"agent_session": public_session(current), "restarted": False}
                )
        return restarted


__all__ = [
    "ACTIVE_RUNTIME_STATES",
    "AttentionReset",
    "RoomStartupSessionReconciler",
    "SessionStarter",
]
