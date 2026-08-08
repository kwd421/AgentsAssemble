from __future__ import annotations

from collections.abc import Callable
from typing import ContextManager

from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.repository import RoomRepository


RoomCleanup = Callable[[str], object]
AgentSessionCleanup = Callable[[str, str], list[str]]
CommandCapabilityCheck = Callable[[dict[str, object], str], None]
CommandPrincipal = Callable[[dict[str, object]], str]
DurableCommand = Callable[
    [
        dict[str, object],
        str,
        str,
        str,
        dict[str, object],
        Callable[[RoomCommandUnitOfWork], dict[str, object]],
    ],
    dict[str, object],
]
OperationId = Callable[[str, str, str, str], str]
PriorCommandAck = Callable[
    [dict[str, object], str, str, str, dict[str, object]],
    dict[str, object],
]


class RoomClosureService:
    """Close a room only after its live access and runtimes are gone."""

    def __init__(
        self,
        *,
        cleanup_agent_sessions: AgentSessionCleanup,
        revoke_room_invites: RoomCleanup,
        revoke_room_sessions: RoomCleanup,
        disconnect_room: RoomCleanup,
        remove_room_providers: RoomCleanup,
    ) -> None:
        self._cleanup_agent_sessions = cleanup_agent_sessions
        self._revoke_room_invites = revoke_room_invites
        self._revoke_room_sessions = revoke_room_sessions
        self._disconnect_room = disconnect_room
        self._remove_room_providers = remove_room_providers

    def prepare(
        self,
        room_id: str,
        *,
        operation_id: str,
    ) -> dict[str, object]:
        warnings = self._cleanup_agent_sessions(room_id, operation_id)
        revoked_invites = int(self._revoke_room_invites(room_id))
        revoked_sessions = int(self._revoke_room_sessions(room_id))
        self._remove_room_providers(room_id)
        self._disconnect_room(room_id)
        return {
            "room_id": room_id,
            "status": "closed",
            "revoked_invites": revoked_invites,
            "revoked_sessions": revoked_sessions,
            "cleanup_warnings": warnings,
        }

    @staticmethod
    def close_in_unit(
        prepared: dict[str, object],
        *,
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        unit.update_room_status("closed")
        unit.append_event("room_closed")
        return dict(prepared)


class RoomLifecycleCommandService:
    """Coordinate canonical close/archive commands and their external effects."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        closure: RoomClosureService,
        lock: ContextManager[object],
        require_capability: CommandCapabilityCheck,
        prior_command_ack: PriorCommandAck,
        execute_durable_command: DurableCommand,
        command_principal: CommandPrincipal,
        operation_id: OperationId,
    ) -> None:
        self._store = store
        self._closure = closure
        self._lock = lock
        self._require_capability = require_capability
        self._prior_command_ack = prior_command_ack
        self._execute_durable_command = execute_durable_command
        self._command_principal = command_principal
        self._operation_id = operation_id

    def handle(
        self,
        identity: dict[str, object],
        room_id: str,
        *,
        request_id: str,
        action: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        self._require_capability(identity, "room.manage")
        if action == "room.close":
            return self._close(
                identity,
                room_id,
                request_id=request_id,
                payload=payload,
            )
        if action == "room.archive":
            archived = bool(payload.get("archived"))
            with self._lock:
                return self._execute_durable_command(
                    identity,
                    room_id,
                    request_id,
                    action,
                    payload,
                    lambda unit: self._archive_in_unit(
                        room_id,
                        archived=archived,
                        unit=unit,
                    ),
                )
        raise RoomCommandRejected(
            f"Unsupported room lifecycle command: {action}",
            code="unknown_action",
        )

    def _close(
        self,
        identity: dict[str, object],
        room_id: str,
        *,
        request_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        with self._lock:
            prior_ack = self._prior_command_ack(
                identity,
                room_id,
                request_id,
                "room.close",
                payload,
            )
            if prior_ack:
                return prior_ack
            if self._store.room(room_id).get("status") == "closed":
                return self._execute_durable_command(
                    identity,
                    room_id,
                    request_id,
                    "room.close",
                    payload,
                    lambda _unit: {
                        "room_id": room_id,
                        "status": "closed",
                        "already_closed": True,
                    },
                )
            operation_id = self._operation_id(
                room_id,
                self._command_principal(identity),
                request_id,
                "room.close",
            )
            prepared = self._closure.prepare(
                room_id,
                operation_id=operation_id,
            )
            return self._execute_durable_command(
                identity,
                room_id,
                request_id,
                "room.close",
                payload,
                lambda unit: self._closure.close_in_unit(
                    prepared,
                    unit=unit,
                ),
            )

    @staticmethod
    def _archive_in_unit(
        room_id: str,
        *,
        archived: bool,
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        status = "archived" if archived else "active"
        unit.update_room_status(status)
        if archived:
            unit.append_event("room_archived")
        return {"room_id": room_id, "status": status}


__all__ = [
    "AgentSessionCleanup",
    "RoomCleanup",
    "RoomClosureService",
    "RoomLifecycleCommandService",
]
