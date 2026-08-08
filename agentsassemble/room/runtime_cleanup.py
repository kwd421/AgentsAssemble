from __future__ import annotations

import hashlib
from collections.abc import Callable

from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text


AgentStopper = Callable[[str, str, str], object]
BridgeChecker = Callable[[str, str], bool]
ParticipantCleanup = Callable[[str, str], object]


class RoomRuntimeCleanupService:
    """Stop or detach every Agent Session whose lifetime belongs to a room."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        has_bridge: BridgeChecker,
        stop_agent: AgentStopper,
        revoke_participant_sessions: ParticipantCleanup,
        disconnect_participant: ParticipantCleanup,
    ) -> None:
        self.store = store
        self._has_bridge = has_bridge
        self._stop_agent = stop_agent
        self._revoke_participant_sessions = revoke_participant_sessions
        self._disconnect_participant = disconnect_participant

    def cleanup(
        self,
        room_id: str,
        *,
        operation_id: str,
        failure_action: str,
    ) -> list[str]:
        cleanup_failures: list[str] = []
        cleanup_warnings: list[str] = []
        for session in list(self.store.sessions(room_id)):
            session_id = clean_room_text(session.get("session_id"), 128)
            if not session_id or session.get("runtime_status") in {
                "stopped",
                "available",
            }:
                continue
            ownership = clean_room_text(
                session.get("process_ownership"),
                32,
            ) or ("external" if session.get("external_owned") else "server")
            if ownership == "external" and not self._has_bridge(room_id, session_id):
                self._revoke_participant_sessions(room_id, session_id)
                self._disconnect_participant(room_id, session_id)
                cleanup_warnings.append(
                    f"{session_id}: external bridge was disconnected; room "
                    "access was revoked without claiming provider shutdown"
                )
                continue
            try:
                self._stop_agent(
                    room_id,
                    session_id,
                    _nested_effect_operation_id(operation_id, session_id),
                )
            except (RoomCommandRejected, ValueError) as error:
                if ownership == "external":
                    cleanup_warnings.append(f"{session_id}: {error}")
                else:
                    cleanup_failures.append(f"{session_id}: {error}")
        if cleanup_failures:
            raise RoomCommandRejected(
                f"Room {failure_action} stopped because Agent Session cleanup failed: "
                + "; ".join(cleanup_failures),
                code="room_cleanup_failed",
            )
        return cleanup_warnings


def _nested_effect_operation_id(
    parent_operation_id: str,
    subject_id: str,
) -> str:
    serialized = "\0".join((parent_operation_id, subject_id))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


__all__ = [
    "AgentStopper",
    "BridgeChecker",
    "ParticipantCleanup",
    "RoomRuntimeCleanupService",
]
