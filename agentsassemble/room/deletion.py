from __future__ import annotations

from typing import Callable

from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text


IdentityRoomLookup = Callable[[str], dict[str, object]]
AgentSessionCleanup = Callable[[str, str], list[str]]
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
        cleanup_agent_sessions: AgentSessionCleanup,
        complete_cleanup: CompleteCleanup,
    ) -> None:
        self.store = store
        self._identity_room = identity_room
        self._cleanup_agent_sessions = cleanup_agent_sessions
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
            canonical_room.get("label")
            or identity_room.get("label")
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
            operation_id,
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

__all__ = [
    "AgentSessionCleanup",
    "CompleteCleanup",
    "IdentityRoomLookup",
    "RoomDeletionService",
]
