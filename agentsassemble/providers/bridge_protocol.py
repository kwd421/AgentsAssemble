from __future__ import annotations

from dataclasses import dataclass

from agentsassemble.providers.runtime_contracts import (
    AUTOMATIC_FINAL,
    EXPLICIT_ROOM_PORTAL,
    PublicationMode,
)
from agentsassemble.room.text import clean_room_text


class BridgeProtocolError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "bridge_protocol_error",
        turn_id: str = "",
        fatal: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.turn_id = turn_id
        self.fatal = fatal


class BridgeReportRejected(RuntimeError):
    def __init__(self, message: str, *, request_id: str, action: str, code: str) -> None:
        super().__init__(message)
        self.request_id = request_id
        self.action = action
        self.code = code or "bridge_report_rejected"


class BridgeReportTimeout(TimeoutError):
    def __init__(self, *, request_id: str, action: str) -> None:
        super().__init__(f"Agent Bridge report {action} timed out waiting for ACK/NACK.")
        self.request_id = request_id
        self.action = action
        self.code = "bridge_report_timeout"


@dataclass(frozen=True)
class TurnAssignmentEnvelope:
    room_id: str
    participant_id: str
    session_id: str
    turn_id: str
    provider_input: str
    timeout_seconds: float
    publication_mode: PublicationMode

    @classmethod
    def parse_strict(
        cls,
        value: object,
        *,
        room_id: str,
        participant_id: str,
        session_id: str,
    ) -> TurnAssignmentEnvelope:
        if not isinstance(value, dict):
            raise BridgeProtocolError("Turn assignment must be an object.", fatal=True)
        turn_id = clean_room_text(value.get("turn_id"), limit=128)
        if not turn_id:
            raise BridgeProtocolError(
                "Turn assignment requires turn_id.",
                code="assignment_turn_id_missing",
                fatal=True,
            )
        actual_room = clean_room_text(value.get("room_id"), limit=128)
        actual_participant = clean_room_text(value.get("participant_id"), limit=128)
        actual_session = clean_room_text(value.get("session_id"), limit=128)
        if (actual_room, actual_participant, actual_session) != (room_id, participant_id, session_id):
            raise BridgeProtocolError(
                "Turn assignment identity does not match this Agent Bridge.",
                code="assignment_identity_mismatch",
                turn_id=turn_id,
                fatal=True,
            )
        provider_input = value.get("provider_input")
        if not isinstance(provider_input, str) or not provider_input.strip():
            raise BridgeProtocolError(
                "Turn assignment requires non-empty provider_input.",
                code="assignment_invalid",
                turn_id=turn_id,
            )
        if value.get("publication_mode") != AUTOMATIC_FINAL:
            raise BridgeProtocolError(
                "Turn assignment requires automatic_final publication.",
                code="assignment_publication_mode_invalid",
                turn_id=turn_id,
                fatal=True,
            )
        timeout_value = value.get("timeout_seconds")
        if isinstance(timeout_value, bool):
            timeout_seconds = 0.0
        else:
            try:
                timeout_seconds = float(timeout_value)
            except (TypeError, ValueError):
                timeout_seconds = 0.0
        if timeout_seconds <= 0:
            raise BridgeProtocolError(
                "Turn assignment requires a positive timeout_seconds.",
                code="assignment_invalid",
                turn_id=turn_id,
            )
        return cls(
            room_id=actual_room,
            participant_id=actual_participant,
            session_id=actual_session,
            turn_id=turn_id,
            provider_input=provider_input,
            timeout_seconds=timeout_seconds,
            publication_mode=AUTOMATIC_FINAL,
        )


@dataclass(frozen=True)
class RoomWakeEnvelope:
    room_id: str
    participant_id: str
    session_id: str
    turn_id: str
    input_up_to_seq: int
    timeout_seconds: float
    attachment_ids: tuple[str, ...]
    publication_mode: PublicationMode

    @classmethod
    def parse_strict(
        cls,
        value: object,
        *,
        room_id: str,
        participant_id: str,
        session_id: str,
    ) -> RoomWakeEnvelope:
        if not isinstance(value, dict):
            raise BridgeProtocolError("Room wake must be an object.", fatal=True)
        turn_id = clean_room_text(value.get("turn_id"), limit=128)
        if not turn_id:
            raise BridgeProtocolError(
                "Room wake requires turn_id.",
                code="room_wake_turn_id_missing",
                fatal=True,
            )
        actual_room = clean_room_text(value.get("room_id"), limit=128)
        actual_participant = clean_room_text(value.get("participant_id"), limit=128)
        actual_session = clean_room_text(value.get("session_id"), limit=128)
        if (actual_room, actual_participant, actual_session) != (
            room_id,
            participant_id,
            session_id,
        ):
            raise BridgeProtocolError(
                "Room wake identity does not match this Agent Bridge.",
                code="room_wake_identity_mismatch",
                turn_id=turn_id,
                fatal=True,
            )
        timeout_value = value.get("timeout_seconds")
        if isinstance(timeout_value, bool):
            timeout_seconds = 0.0
        else:
            try:
                timeout_seconds = float(timeout_value)
            except (TypeError, ValueError):
                timeout_seconds = 0.0
        if timeout_seconds <= 0:
            raise BridgeProtocolError(
                "Room wake requires a positive timeout_seconds.",
                code="room_wake_invalid",
                turn_id=turn_id,
            )
        if "provider_input" in value:
            raise BridgeProtocolError(
                "Room wake must not contain provider transcript input.",
                code="room_wake_contains_provider_input",
                turn_id=turn_id,
                fatal=True,
            )
        if value.get("publication_mode") != EXPLICIT_ROOM_PORTAL:
            raise BridgeProtocolError(
                "Room wake requires explicit_room_portal publication.",
                code="room_wake_publication_mode_invalid",
                turn_id=turn_id,
                fatal=True,
            )
        input_up_to_seq = value.get("input_up_to_seq")
        if (
            isinstance(input_up_to_seq, bool)
            or not isinstance(input_up_to_seq, int)
            or input_up_to_seq < 0
        ):
            raise BridgeProtocolError(
                "Room wake requires a non-negative input_up_to_seq.",
                code="room_wake_invalid",
                turn_id=turn_id,
            )
        attachment_values = value.get("attachment_ids")
        if attachment_values in (None, ""):
            attachment_values = []
        if not isinstance(attachment_values, list):
            raise BridgeProtocolError(
                "Room wake attachment_ids must be a list.",
                code="room_wake_invalid",
                turn_id=turn_id,
            )
        attachment_ids = tuple(
            dict.fromkeys(
                attachment_id
                for item in attachment_values
                if (
                    attachment_id := clean_room_text(item, limit=64)
                )
            )
        )
        return cls(
            room_id=actual_room,
            participant_id=actual_participant,
            session_id=actual_session,
            turn_id=turn_id,
            input_up_to_seq=input_up_to_seq,
            timeout_seconds=timeout_seconds,
            attachment_ids=attachment_ids,
            publication_mode=EXPLICIT_ROOM_PORTAL,
        )


@dataclass(frozen=True)
class BridgeReportResponse:
    request_id: str
    accepted: bool
    code: str
    message: str
    frame: dict[str, object]

    @classmethod
    def parse(cls, value: object) -> BridgeReportResponse | None:
        if not isinstance(value, dict) or value.get("op") not in {"ack", "nack"}:
            return None
        request_id = clean_room_text(value.get("request_id"), limit=128)
        if not request_id:
            return None
        if value.get("op") == "ack":
            return cls(request_id=request_id, accepted=True, code="", message="", frame=dict(value))
        error = value.get("error") if isinstance(value.get("error"), dict) else {}
        code = clean_room_text(error.get("code"), limit=128) or "bridge_report_rejected"
        message = clean_room_text(error.get("message"), limit=1000) or code
        return cls(
            request_id=request_id,
            accepted=False,
            code=code,
            message=message,
            frame=dict(value),
        )
