from __future__ import annotations

from collections.abc import Callable
import threading

from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room import bridge_diagnostics
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.event_broker import RoomEventBroker
from agentsassemble.room.identity import room_identity_principals
from agentsassemble.room.provider_request_contract import (
    PROVIDER_REQUEST_TERMINAL_STATUSES,
    ProviderRequestValidationError,
    normalize_provider_request,
    normalize_provider_resolution,
)
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text


BridgeSession = Callable[[dict[str, object], str], tuple[str, dict[str, object]]]
DurableCommand = Callable[[Callable[[RoomCommandUnitOfWork], dict[str, object]]], dict[str, object]]
PROVIDER_REQUEST_ACTIONS = frozenset(
    {"provider.request.open", "provider.request.resolve", "provider.request.closed"}
)


class RoomProviderRequestService:
    """Own pending provider requests and their one-use room resolution path."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        broker: RoomEventBroker,
        bridge_session: BridgeSession,
        lock: threading.RLock,
        redact_public_payload: bridge_diagnostics.PublicPayloadRedactor | None = None,
    ) -> None:
        self.store = store
        self.broker = broker
        self._bridge_session = bridge_session
        self._lock = lock
        self._redact_public_payload = (
            redact_public_payload or bridge_diagnostics.default_public_payload_redactor
        )

    def handle_command(
        self,
        identity: dict[str, object],
        room_id: str,
        action: str,
        payload: dict[str, object],
        *,
        can_resolve: bool,
        execute: DurableCommand,
    ) -> dict[str, object]:
        if action == "provider.request.resolve" and not can_resolve:
            raise RoomCommandRejected(
                "The session cannot resolve provider requests.",
                code="permission_denied",
            )
        operation = {
            "provider.request.open": self.open_in_unit,
            "provider.request.resolve": self.resolve_in_unit,
            "provider.request.closed": self.close_in_unit,
        }[action]
        with self._lock:
            ack = execute(
                lambda unit: operation(identity, room_id, payload, unit=unit)
            )
        if action != "provider.request.resolve" or ack.get("deduplicated"):
            return ack
        result = ack.get("result") if isinstance(ack.get("result"), dict) else {}
        if not self.deliver_resolution(room_id, result):
            self.mark_delivery_failed(
                room_id,
                clean_room_text(result.get("provider_request_id"), limit=128),
            )
        return ack

    def open_in_unit(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
        *,
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        agent_id, _ = self._bridge_session(identity, room_id)
        session_id = clean_room_text(identity.get("session_id") or agent_id, limit=128)
        session = unit.session(session_id)
        if isinstance(session.get("pending_provider_request"), dict) and session.get(
            "pending_provider_request"
        ):
            raise RoomCommandRejected(
                "This Agent Session already has a pending provider request.",
                code="provider_request_conflict",
            )
        request = self._normalized_request(
            self._redact_public_payload(room_id, session_id, payload)
        )
        participant = unit.participant(agent_id)
        owner_id = clean_room_text(
            participant.get("owner_id") or participant.get("created_by"),
            limit=128,
        )
        pending = {
            **request,
            "status": "open",
            "participant_id": agent_id,
            "session_id": session_id,
            "owner_id": owner_id,
        }
        unit.update_session_fields(session_id, pending_provider_request=pending)
        event = unit.append_event(
            "provider_request_opened",
            participant_id=agent_id,
            session_id=session_id,
            owner_id=owner_id,
            audience="owner",
            provider_request={**request, "status": "open"},
        )
        return {
            "status": "open",
            "provider_request_id": request["provider_request_id"],
            "event": event,
        }

    def resolve_in_unit(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
        *,
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        request_id = clean_room_text(payload.get("provider_request_id"), limit=128)
        pending, session = self._pending_request(room_id, request_id, unit=unit)
        self._require_owner(identity, room_id, pending, unit=unit)
        if pending.get("status") != "open":
            raise RoomCommandRejected(
                "This provider request is already being resolved.",
                code="provider_request_already_resolving",
            )
        participant_id = clean_room_text(pending.get("participant_id"), limit=128)
        if not self.broker.has_bridge(room_id, participant_id):
            raise RoomCommandRejected(
                "The Agent Session disconnected before the request could be resolved.",
                code="provider_request_bridge_unavailable",
            )
        resolution = self._normalized_resolution(pending, payload)
        resolving = {**pending, "status": "resolving", "resolution": resolution}
        unit.update_session_fields(
            clean_room_text(session.get("session_id"), limit=128),
            pending_provider_request=resolving,
        )
        event = unit.append_event(
            "provider_request_resolution_requested",
            participant_id=participant_id,
            session_id=clean_room_text(session.get("session_id"), limit=128),
            owner_id=clean_room_text(pending.get("owner_id"), limit=128),
            audience="owner",
            provider_request={
                "provider_request_id": request_id,
                "status": "resolving",
            },
        )
        return {
            "status": "resolving",
            "provider_request_id": request_id,
            "participant_id": participant_id,
            "resolution": resolution,
            "event": event,
        }

    def deliver_resolution(self, room_id: str, result: dict[str, object]) -> bool:
        participant_id = clean_room_text(result.get("participant_id"), limit=128)
        request_id = clean_room_text(result.get("provider_request_id"), limit=128)
        resolution = result.get("resolution") if isinstance(result.get("resolution"), dict) else {}
        return self.broker.direct_to_bridge(
            room_id,
            participant_id,
            {
                "op": "provider.request.resolve",
                "provider_request_id": request_id,
                **resolution,
            },
        )

    def close_in_unit(
        self,
        identity: dict[str, object],
        room_id: str,
        payload: dict[str, object],
        *,
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        agent_id, _ = self._bridge_session(identity, room_id)
        request_id = clean_room_text(payload.get("provider_request_id"), limit=128)
        pending, session = self._pending_request(room_id, request_id, unit=unit)
        if clean_room_text(pending.get("participant_id"), limit=128) != agent_id:
            raise RoomCommandRejected(
                "Provider request ownership does not match this bridge.",
                code="permission_denied",
            )
        status = clean_room_text(payload.get("status"), limit=32)
        if status not in PROVIDER_REQUEST_TERMINAL_STATUSES:
            raise RoomCommandRejected(
                "Unsupported provider request completion status.",
                code="invalid_provider_request_status",
            )
        unit.update_session_fields(
            clean_room_text(session.get("session_id"), limit=128),
            pending_provider_request={},
        )
        event = unit.append_event(
            "provider_request_resolved",
            participant_id=agent_id,
            session_id=clean_room_text(session.get("session_id"), limit=128),
            owner_id=clean_room_text(pending.get("owner_id"), limit=128),
            audience="owner",
            provider_request={
                "provider_request_id": request_id,
                "status": status,
            },
        )
        return {
            "status": status,
            "provider_request_id": request_id,
            "event": event,
        }

    def mark_delivery_failed(self, room_id: str, request_id: str) -> None:
        for session in self.store.sessions(room_id):
            pending = session.get("pending_provider_request")
            if not isinstance(pending, dict) or pending.get("provider_request_id") != request_id:
                continue
            fail_pending_provider_request(
                self.store,
                room_id,
                clean_room_text(session.get("session_id"), limit=128),
                reason_code="provider_request_bridge_unavailable",
            )
            return

    def _pending_request(
        self,
        room_id: str,
        request_id: str,
        *,
        unit: RoomCommandUnitOfWork,
    ) -> tuple[dict[str, object], dict[str, object]]:
        if not request_id:
            raise RoomCommandRejected(
                "provider_request_id is required.",
                code="provider_request_not_pending",
            )
        for session in self.store.sessions(room_id):
            pending = session.get("pending_provider_request")
            if isinstance(pending, dict) and pending.get("provider_request_id") == request_id:
                current = unit.session(clean_room_text(session.get("session_id"), limit=128))
                current_pending = current.get("pending_provider_request")
                if isinstance(current_pending, dict) and current_pending.get(
                    "provider_request_id"
                ) == request_id:
                    return dict(current_pending), current
        raise RoomCommandRejected(
            "The provider request is no longer pending.",
            code="provider_request_not_pending",
        )

    def _require_owner(
        self,
        identity: dict[str, object],
        room_id: str,
        pending: dict[str, object],
        *,
        unit: RoomCommandUnitOfWork,
    ) -> None:
        participant_id = clean_room_text(pending.get("participant_id"), limit=128)
        participant = unit.participant(participant_id)
        owner_id = clean_room_text(
            participant.get("owner_id") or pending.get("owner_id"),
            limit=128,
        )
        principals = room_identity_principals(identity)
        if owner_id not in principals:
            raise RoomCommandRejected(
                "Only this Agent Session's owner may resolve its provider request.",
                code="permission_denied",
            )

    @staticmethod
    def _normalized_request(payload: dict[str, object]) -> dict[str, object]:
        try:
            return normalize_provider_request(payload)
        except ProviderRequestValidationError as error:
            raise RoomCommandRejected(str(error), code=error.code) from error

    @staticmethod
    def _normalized_resolution(
        request: dict[str, object],
        payload: dict[str, object],
    ) -> dict[str, object]:
        try:
            return normalize_provider_resolution(request, payload)
        except ProviderRequestValidationError as error:
            raise RoomCommandRejected(str(error), code=error.code) from error


def fail_pending_provider_request(
    store: RoomRepository,
    room_id: str,
    session_id: str,
    *,
    reason_code: str,
) -> bool:
    """Fail one durable request after its bridge-side responder is gone."""

    clean_session_id = clean_room_text(session_id, limit=128)
    if not clean_session_id:
        return False
    with store.transaction(room_id) as transaction:
        session = transaction.session(clean_session_id)
        pending = session.get("pending_provider_request")
        if not isinstance(pending, dict) or not pending:
            return False
        request_id = clean_room_text(
            pending.get("provider_request_id"),
            limit=128,
        )
        transaction.update_session_fields(
            clean_session_id,
            pending_provider_request={},
        )
        transaction.append_event(
            "provider_request_resolved",
            participant_id=clean_room_text(
                pending.get("participant_id") or session.get("participant_id"),
                limit=128,
            ),
            session_id=clean_session_id,
            owner_id=clean_room_text(pending.get("owner_id"), limit=128),
            audience="owner",
            provider_request={
                "provider_request_id": request_id,
                "status": "failed",
            },
            reason_code=clean_room_text(reason_code, limit=128),
        )
        return True

__all__ = [
    "PROVIDER_REQUEST_ACTIONS",
    "RoomProviderRequestService",
    "fail_pending_provider_request",
]
