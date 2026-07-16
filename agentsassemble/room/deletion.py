from __future__ import annotations

import hashlib
from typing import Callable

from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text


AgentStopper = Callable[[str, str, str], object]
BridgeChecker = Callable[[str, str], bool]
ParticipantCleanup = Callable[[str, str], object]
IdentityRoomLookup = Callable[[str], dict[str, object]]
CompleteCleanup = Callable[
    [str, str, dict[str, object], bool],
    dict[str, object],
]


class RoomDeletionService:
    """Own room-delete validation, provider cleanup, and tombstone resumption."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        identity_room: IdentityRoomLookup,
        has_bridge: BridgeChecker,
        stop_agent: AgentStopper,
        revoke_participant_sessions: ParticipantCleanup,
        disconnect_participant: ParticipantCleanup,
        complete_cleanup: CompleteCleanup,
    ) -> None:
        self.store = store
        self._identity_room = identity_room
        self._has_bridge = has_bridge
        self._stop_agent = stop_agent
        self._revoke_participant_sessions = revoke_participant_sessions
        self._disconnect_participant = disconnect_participant
        self._complete_cleanup = complete_cleanup

    def delete(
        self,
        room_id: str,
        confirmation_name: str,
        *,
        is_owner: bool,
        request_id: str,
        principal_id: str,
        payload_hash: str,
        operation_id: str,
    ) -> dict[str, object]:
        if not is_owner:
            raise RoomCommandRejected(
                "Only the room owner can delete this server.",
                code="permission_denied",
            )
        identity_room = self._identity_room(room_id)
        canonical_room = self.store.room(room_id)
        room_name = clean_room_text(
            identity_room.get("label")
            or canonical_room.get("label")
            or room_id,
            128,
        )
        if confirmation_name != room_name:
            raise RoomCommandRejected(
                "The confirmation name does not match the server name.",
                code="confirmation_mismatch",
            )
        cleanup_warnings = self._cleanup_agent_sessions(
            room_id,
            operation_id=operation_id,
        )
        result = {
            "room_id": room_id,
            "deleted": True,
            "cleanup_warnings": cleanup_warnings,
        }
        ack = {
            "op": "ack",
            "request_id": request_id,
            "accepted": True,
            "action": "room.delete",
            "result": result,
            "deduplicated": False,
        }
        deleted = self.store.delete_room(
            room_id,
            reason="owner deleted server",
            tombstone={
                "principal_id": principal_id,
                "request_id": request_id,
                "action": "room.delete",
                "payload_hash": payload_hash,
                "result": ack,
            },
            cleanup_status="pending",
            room_name=room_name,
        )
        if not deleted:
            raise RoomCommandRejected(
                "The room no longer exists.",
                code="room_deleted",
            )
        return self._complete_cleanup(
            room_id,
            room_name,
            ack,
            False,
        )

    def resume(
        self,
        room_id: str,
        *,
        principal_id: str,
        request_id: str,
        payload_hash: str,
        tombstone: dict[str, object],
    ) -> dict[str, object]:
        if (
            tombstone.get("principal_id") != principal_id
            or tombstone.get("request_id") != request_id
            or tombstone.get("action") != "room.delete"
        ):
            raise RoomCommandRejected(
                "The room was deleted.",
                code="room_deleted",
            )
        if tombstone.get("payload_hash") != payload_hash:
            raise RoomCommandRejected(
                "request_id was already used for a different command.",
                code="idempotency_conflict",
            )
        ack = dict(tombstone.get("result") or {})
        if not ack:
            raise RoomCommandRejected(
                "The room was deleted.",
                code="room_deleted",
            )
        if tombstone.get("cleanup_status") != "complete":
            ack = self._complete_cleanup(
                room_id,
                clean_room_text(
                    tombstone.get("room_name"),
                    128,
                )
                or room_id,
                ack,
                True,
            )
        return {
            **ack,
            "deduplicated": True,
        }

    def _cleanup_agent_sessions(
        self,
        room_id: str,
        *,
        operation_id: str,
    ) -> list[str]:
        cleanup_failures: list[str] = []
        cleanup_warnings: list[str] = []
        for session in list(self.store.sessions(room_id)):
            session_id = clean_room_text(
                session.get("session_id"),
                128,
            )
            if not session_id or session.get("runtime_status") in {
                "stopped",
                "available",
            }:
                continue
            ownership = clean_room_text(
                session.get("process_ownership"),
                32,
            ) or (
                "external"
                if session.get("external_owned")
                else "server"
            )
            if (
                ownership == "external"
                and not self._has_bridge(room_id, session_id)
            ):
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
                    _nested_effect_operation_id(
                        operation_id,
                        session_id,
                    ),
                )
            except (RoomCommandRejected, ValueError) as error:
                if ownership == "external":
                    cleanup_warnings.append(f"{session_id}: {error}")
                else:
                    cleanup_failures.append(f"{session_id}: {error}")
        if cleanup_failures:
            raise RoomCommandRejected(
                "Room deletion stopped because Agent Session cleanup failed: "
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
    "CompleteCleanup",
    "IdentityRoomLookup",
    "ParticipantCleanup",
    "RoomDeletionService",
]
