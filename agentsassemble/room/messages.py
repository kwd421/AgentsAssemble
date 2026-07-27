from __future__ import annotations

from agentsassemble.room.attachments import AttachmentError, FileAttachmentStore
from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.text import clean_room_text
from agentsassemble.room.turn_coordinator import room_message_text


class RoomMessageService:
    """Validate and append one canonical human room message."""

    def __init__(self, attachments: FileAttachmentStore) -> None:
        self._attachments = attachments

    def send_in_unit(
        self,
        identity: dict[str, object],
        payload: dict[str, object],
        *,
        unit: RoomCommandUnitOfWork,
        compatibility_muted: bool,
    ) -> dict[str, object]:
        content = room_message_text(
            payload.get("content") or payload.get("message"),
            limit=12000,
        )
        kind = clean_room_text(payload.get("kind"), 64) or "message"
        try:
            attachments = self._attachments.normalize_references(
                payload.get("attachments"),
                room_id=unit.room_id,
            )
        except AttachmentError as error:
            raise RoomCommandRejected(
                str(error),
                code="invalid_attachment",
            ) from error
        if kind not in {"vote", "vote_cast"} and not content and not attachments:
            raise RoomCommandRejected(
                "Message content or an attachment is required.",
                code="empty",
            )
        participant_id = clean_room_text(identity.get("agent_id"), 128)
        participant = unit.participant(participant_id)
        if participant.get("status") in {"kicked", "left"}:
            raise RoomCommandRejected(
                "This participant is no longer in the room.",
                code="session_revoked",
            )
        canonical_muted = (
            bool(participant.get("muted"))
            if "muted" in participant
            else compatibility_muted
        )
        if canonical_muted:
            raise RoomCommandRejected(
                "You are muted by the room host.",
                code="muted",
            )
        event = unit.append_event(
            "message_final",
            participant_id=participant_id,
            participant_type="human",
            actor_id=participant_id,
            actor_type="human",
            display_name=(
                clean_room_text(identity.get("display_name"), 64)
                or participant_id
            ),
            content=content,
            message_kind=kind,
            attachments=attachments,
            vote_id=payload.get("vote_id"),
            vote_question=payload.get("vote_question"),
            vote_options=payload.get("vote_options"),
            vote_choice=payload.get("vote_choice"),
            target_agent_id=payload.get("target_agent_id"),
            relay_depth=0,
        )
        return {"event": event, "event_seq": event["seq"]}


__all__ = ["RoomMessageService"]
