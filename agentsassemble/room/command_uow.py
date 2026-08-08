from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from contextlib import AbstractContextManager
from types import TracebackType

from agentsassemble.room_attention import AgentAttentionState
from agentsassemble.room.global_settings import RoomGlobalSettingsRecord
from agentsassemble.room.repository import RoomRepository, RoomTransaction


class RoomCommandIdempotencyConflict(ValueError):
    pass


class RoomCommandNotFinalized(RuntimeError):
    pass


def command_payload_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RoomCommandUnitOfWork:
    """Own one durable room command transaction without exposing backend handles."""

    def __init__(
        self,
        repository: RoomRepository,
        *,
        room_id: str,
        principal_id: str,
        request_id: str,
        action: str,
        payload: Mapping[str, object],
    ) -> None:
        self._repository = repository
        self.room_id = room_id
        self.principal_id = principal_id
        self.request_id = request_id
        self.action = action
        self.payload_hash = command_payload_hash(payload)
        self._scope: AbstractContextManager[RoomTransaction] | None = None
        self._transaction: RoomTransaction | None = None
        self._prior_ack: dict[str, object] = {}
        self._ack: dict[str, object] = {}
        self._recorded = False

    def __enter__(self) -> RoomCommandUnitOfWork:
        if self._scope is not None:
            raise RuntimeError("RoomCommandUnitOfWork cannot be reused.")
        self._scope = self._repository.transaction(self.room_id)
        self._transaction = self._scope.__enter__()
        try:
            prior = self._transaction.command_record(self.principal_id, self.request_id)
            if prior:
                if prior.get("action") != self.action or prior.get("payload_hash") != self.payload_hash:
                    raise RoomCommandIdempotencyConflict(
                        "request_id was already used for a different command."
                    )
                self._prior_ack = {**dict(prior.get("result") or {}), "deduplicated": True}
        except BaseException as error:
            self._scope.__exit__(type(error), error, error.__traceback__)
            self._transaction = None
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        scope = self._require_scope()
        if exc_type is None and not self.deduplicated and not self._recorded:
            error = RoomCommandNotFinalized(
                "A new room command must record its ACK before the transaction can commit."
            )
            scope.__exit__(type(error), error, error.__traceback__)
            self._transaction = None
            raise error
        try:
            return scope.__exit__(exc_type, exc, traceback)
        finally:
            self._transaction = None

    @property
    def deduplicated(self) -> bool:
        return bool(self._prior_ack)

    def resolved_ack(self) -> dict[str, object]:
        if self.deduplicated:
            return dict(self._prior_ack)
        if not self._recorded:
            raise RoomCommandNotFinalized("The room command ACK has not been recorded.")
        return dict(self._ack)

    def participant(self, participant_id: str) -> dict[str, object]:
        return self._require_transaction().participant(participant_id)

    def session(self, session_id: str) -> dict[str, object]:
        return self._require_transaction().session(session_id)

    def event_by_id(self, event_id: str) -> dict[str, object]:
        return self._require_transaction().event_by_id(event_id)

    def room_settings(self) -> RoomGlobalSettingsRecord:
        return self._require_transaction().room_settings()

    def update_room_settings(
        self,
        updates: dict[str, object],
    ) -> RoomGlobalSettingsRecord:
        return self._require_transaction().update_room_settings(updates)

    def update_room_status(self, status: str) -> dict[str, object]:
        return self._require_transaction().update_room_status(status)

    def upsert_participant(self, participant: dict[str, object]) -> tuple[dict[str, object], bool]:
        return self._require_transaction().upsert_participant(participant)

    def update_participant_fields(self, participant_id: str, **updates: object) -> dict[str, object]:
        return self._require_transaction().update_participant_fields(participant_id, **updates)

    def detach_participant_sessions(
        self,
        participant_id: str,
    ) -> list[dict[str, object]]:
        return self._require_transaction().detach_participant_sessions(participant_id)

    def upsert_session(self, session: dict[str, object]) -> tuple[dict[str, object], bool]:
        return self._require_transaction().upsert_session(session)

    def update_session_fields(self, session_id: str, **updates: object) -> dict[str, object]:
        return self._require_transaction().update_session_fields(session_id, **updates)

    def append_event(self, event_type: str, **payload: object) -> dict[str, object]:
        return self._require_transaction().append_event(event_type, **payload)

    def advance_attention_state(
        self,
        participant_id: str,
        *,
        observed_seq: int | None = None,
        attention_evaluated_seq: int | None = None,
        provider_sync_seq: int | None = None,
        spoke_seq: int | None = None,
    ) -> AgentAttentionState:
        return self._require_transaction().advance_attention_state(
            participant_id,
            observed_seq=observed_seq,
            attention_evaluated_seq=attention_evaluated_seq,
            provider_sync_seq=provider_sync_seq,
            spoke_seq=spoke_seq,
        )

    def attention_state(self, participant_id: str) -> AgentAttentionState:
        return self._require_transaction().attention_state(participant_id)

    def resolve_attention_lease(
        self,
        lease_id: str,
        *,
        status: str,
    ) -> dict[str, object]:
        return self._require_transaction().resolve_attention_lease(
            lease_id,
            status=status,
        )

    def build_ack(self, result: dict[str, object]) -> dict[str, object]:
        if self.deduplicated:
            raise RuntimeError("A deduplicated command already has a durable ACK.")
        if self._ack:
            raise RuntimeError("The room command ACK was already built.")
        self._ack = {
            "op": "ack",
            "request_id": self.request_id,
            "accepted": True,
            "action": self.action,
            "result": dict(result),
            "deduplicated": False,
        }
        return dict(self._ack)

    def record_ack(self) -> dict[str, object]:
        if not self._ack:
            raise RoomCommandNotFinalized("Build the room command ACK before recording it.")
        if self._recorded:
            return dict(self._ack)
        recorded = self._require_transaction().record_command_result(
            self.request_id,
            self._ack,
            principal_id=self.principal_id,
            action=self.action,
            payload_hash=self.payload_hash,
        )
        self._recorded = True
        return dict(recorded)

    def _require_scope(self) -> AbstractContextManager[RoomTransaction]:
        if self._scope is None:
            raise RuntimeError("RoomCommandUnitOfWork has not been entered.")
        return self._scope

    def _require_transaction(self) -> RoomTransaction:
        if self._transaction is None:
            raise RuntimeError("RoomCommandUnitOfWork is not active.")
        return self._transaction


__all__ = [
    "RoomCommandIdempotencyConflict",
    "RoomCommandNotFinalized",
    "RoomCommandUnitOfWork",
    "command_payload_hash",
]
