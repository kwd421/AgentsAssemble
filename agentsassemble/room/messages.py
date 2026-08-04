from __future__ import annotations

from uuid import uuid4

from agentsassemble.room.attachments import AttachmentError, FileAttachmentStore
from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.random import (
    RoomRandomError,
    choose_random,
    roll_dice,
)
from agentsassemble.room.system_results import (
    RoomSystemResultError,
    prepare_room_system_result,
)
from agentsassemble.room.text import clean_room_text
from agentsassemble.room.tool_authorization import require_room_random_tools
from agentsassemble.room.turn_coordinator import room_message_text
from agentsassemble.room.votes import (
    deadline_for_vote,
    normalize_vote_definition,
    normalize_vote_duration_seconds,
    resolve_vote_choice,
    vote_deadline_has_passed,
    vote_poll,
)


class RoomMessageService:
    """Validate and append participant messages and official random results."""

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
        participant = self._require_active_participant(
            identity,
            unit=unit,
            compatibility_muted=compatibility_muted,
        )
        participant_id = clean_room_text(participant.get("participant_id"), 128)
        display_name = (
            clean_room_text(participant.get("display_name"), 64)
            or clean_room_text(identity.get("display_name"), 64)
            or participant_id
        )
        vote_duration_seconds: int | None = None
        vote_deadline_at = ""
        vote_id = payload.get("vote_id")
        vote_question = payload.get("vote_question")
        vote_options = payload.get("vote_options")
        vote_choice = payload.get("vote_choice")
        if kind == "vote":
            try:
                vote_question, vote_options = normalize_vote_definition(
                    payload.get("vote_question"),
                    payload.get("vote_options"),
                )
            except ValueError as error:
                raise RoomCommandRejected(
                    str(error),
                    code="invalid_vote",
                ) from error
            try:
                vote_duration_seconds = normalize_vote_duration_seconds(
                    payload.get("vote_duration_seconds")
                )
            except ValueError as error:
                raise RoomCommandRejected(
                    str(error),
                    code="invalid_vote_duration",
                ) from error
            vote_deadline_at = deadline_for_vote(vote_duration_seconds)
            vote_id = None
            vote_choice = None
        if kind == "vote_cast":
            vote_id = clean_room_text(payload.get("vote_id"), 128)
            try:
                poll = vote_poll(unit.event_by_id(vote_id), vote_id)
            except ValueError as error:
                raise RoomCommandRejected(
                    str(error),
                    code="vote_not_found",
                ) from error
            try:
                expired = vote_deadline_has_passed(poll.get("vote_deadline_at"))
            except ValueError as error:
                raise RoomCommandRejected(
                    str(error),
                    code="invalid_vote_deadline",
                ) from error
            if expired:
                raise RoomCommandRejected(
                    "This vote has ended.",
                    code="vote_expired",
                )
            vote_choice = resolve_vote_choice(
                payload.get("vote_choice"),
                list(poll.get("vote_options") or []),
            )
            if not vote_choice:
                raise RoomCommandRejected(
                    "vote_choice must match one of the vote options.",
                    code="invalid_vote_choice",
                )
            vote_question = None
            vote_options = None
            content = ""
        event = unit.append_event(
            "message_final",
            participant_id=participant_id,
            participant_type="human",
            actor_id=participant_id,
            actor_type="human",
            display_name=display_name,
            content=content,
            message_kind=kind,
            attachments=attachments,
            vote_id=vote_id,
            vote_question=vote_question,
            vote_options=vote_options,
            vote_duration_seconds=vote_duration_seconds,
            vote_deadline_at=vote_deadline_at,
            vote_choice=vote_choice,
            target_agent_id=(
                None if kind == "vote_cast" else payload.get("target_agent_id")
            ),
            relay_depth=0,
        )
        return {"event": event, "event_seq": event["seq"]}

    def publish_random_result_in_unit(
        self,
        identity: dict[str, object],
        payload: dict[str, object],
        *,
        operation: str,
        unit: RoomCommandUnitOfWork,
        compatibility_muted: bool,
    ) -> dict[str, object]:
        """Generate and append one canonical system randomness result."""

        require_room_random_tools(unit.room_settings())
        participant = self._require_active_participant(
            identity,
            unit=unit,
            compatibility_muted=compatibility_muted,
        )
        participant_id = clean_room_text(participant.get("participant_id"), 128)
        display_name = (
            clean_room_text(participant.get("display_name"), 64)
            or clean_room_text(identity.get("display_name"), 64)
            or participant_id
        )
        reason = clean_room_text(payload.get("reason"), 200)
        try:
            if operation == "roll_dice":
                details = roll_dice(payload.get("notation"))
            elif operation == "choose_random":
                options = payload.get("options")
                if not isinstance(options, list):
                    raise RoomRandomError("Random choice requires a list of options.")
                details = choose_random(options)
            else:
                raise RoomRandomError("Unsupported room randomness operation.")
            if reason:
                details["reason"] = reason
            prepared = prepare_room_system_result(
                result_id=f"result-{uuid4().hex}",
                operation=operation,
                details=details,
                participant_id=participant_id,
                display_name=display_name,
                source_turn_id="",
            )
        except (RoomRandomError, RoomSystemResultError) as error:
            raise RoomCommandRejected(
                str(error),
                code="invalid_room_random_request",
            ) from error
        event = unit.append_event(
            "message_final",
            participant_id="room-system",
            participant_type="system",
            actor_id="room-system",
            actor_type="system",
            display_name=prepared.display_name,
            content=prepared.content,
            message_kind="system",
            message_source="room_tool_result",
            metadata=prepared.metadata,
        )
        return {"event": event, "event_seq": event["seq"]}

    @staticmethod
    def _require_active_participant(
        identity: dict[str, object],
        *,
        unit: RoomCommandUnitOfWork,
        compatibility_muted: bool,
    ) -> dict[str, object]:
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
        return participant


__all__ = ["RoomMessageService"]
