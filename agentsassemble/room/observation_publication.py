"""Server-owned proof that a room observation published through RoomPortal."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from agentsassemble.room.command_uow import RoomCommandUnitOfWork, command_payload_hash
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.structured_messages import (
    StructuredMessageError,
    StructuredRoomMessage,
    prepare_structured_message,
)
from agentsassemble.room.text import clean_room_text


ActiveTurnInWriter = Callable[..., tuple[str, dict[str, object]]]
RequireActivePhase = Callable[[dict[str, object]], str]
RequireObservationReceipt = Callable[..., None]


class RoomObservationPublication:
    """Stage and verify content-bound RoomPortal publication proofs."""

    def __init__(
        self,
        *,
        active_turn_in_writer: ActiveTurnInWriter,
        require_active_phase: RequireActivePhase,
        require_observation_receipt: RequireObservationReceipt,
    ) -> None:
        self._active_turn_in_writer = active_turn_in_writer
        self._require_active_phase = require_active_phase
        self._require_observation_receipt = require_observation_receipt

    def stage_in_unit(
        self,
        identity: dict[str, object],
        payload: dict[str, object],
        *,
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        _agent_id, session = self._active_turn_in_writer(
            identity,
            payload,
            writer=unit,
        )
        self._require_active_phase(session)
        if clean_room_text(session.get("provider_input_mode"), limit=32) != "room_observation":
            raise RoomCommandRejected(
                "Only a room observation can stage RoomPortal publication.",
                code="invalid_state",
            )
        self._require_observation_receipt(
            identity,
            unit.room_id,
            payload,
            session=session,
        )
        try:
            structured = prepare_structured_message(payload)
        except StructuredMessageError as error:
            raise RoomCommandRejected(str(error), code=error.code) from error
        if session.get("room_publication_proof"):
            raise RoomCommandRejected(
                "A RoomPortal publication is already staged for this turn.",
                code="observation_publication_already_staged",
            )
        proof = uuid4().hex
        unit.update_session_fields(
            str(session["session_id"]),
            room_publication_proof=proof,
            room_publication_digest=_publication_digest(payload, structured=structured),
            room_publication_turn_id=str(session["active_turn_id"]),
        )
        return {"publication_proof": proof, "turn_id": str(session["active_turn_id"])}

    @staticmethod
    def require_matching_final(
        payload: dict[str, object],
        *,
        session: dict[str, object],
        structured: StructuredRoomMessage | None,
    ) -> None:
        if clean_room_text(session.get("provider_input_mode"), limit=32) != "room_observation":
            return
        expected_proof = clean_room_text(session.get("room_publication_proof"), limit=128)
        supplied_proof = clean_room_text(payload.get("publication_proof"), limit=128)
        expected_turn_id = clean_room_text(
            session.get("room_publication_turn_id"),
            limit=128,
        )
        if not expected_proof or not supplied_proof:
            raise RoomCommandRejected(
                "Room observations require a staged RoomPortal publication.",
                code="observation_publication_required",
            )
        if (
            supplied_proof != expected_proof
            or expected_turn_id != str(session.get("active_turn_id") or "")
            or structured is None
            or _publication_digest(payload, structured=structured)
            != str(session.get("room_publication_digest") or "")
        ):
            raise RoomCommandRejected(
                "The final message does not match the staged RoomPortal publication.",
                code="observation_publication_mismatch",
            )


def _publication_fields(
    payload: dict[str, object],
    *,
    structured: StructuredRoomMessage,
) -> dict[str, object]:
    return {
        "turn_id": clean_room_text(payload.get("turn_id"), limit=128),
        "observed_through_seq": _safe_bounded_int(
            payload.get("observed_through_seq"),
            default=0,
            minimum=0,
        ),
        "target_agent_id": (
            ""
            if structured.message_kind == "vote_cast"
            else clean_room_text(payload.get("target_agent_id"), limit=128)
        ),
        "content": structured.content,
        "message_kind": structured.message_kind,
        "vote_id": structured.vote_id,
        "vote_question": structured.vote_question,
        "vote_options": list(structured.vote_options),
        "vote_duration_seconds": structured.vote_duration_seconds,
        "vote_choice": structured.vote_choice,
    }


def _publication_digest(
    payload: dict[str, object],
    *,
    structured: StructuredRoomMessage,
) -> str:
    return command_payload_hash(_publication_fields(payload, structured=structured))


def _safe_bounded_int(value: object, *, default: int, minimum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(int(minimum), parsed)


__all__ = ["RoomObservationPublication"]
