from __future__ import annotations

from uuid import uuid4

from agentsassemble.room.attachments import AttachmentError, FileAttachmentStore
from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.identity import room_identity_principals
from agentsassemble.room.random import (
    RoomRandomError,
    choose_random,
    roll_dice,
)
from agentsassemble.room.repository_records import ACTIVE_PARTICIPANT_STATUSES, utc_now
from agentsassemble.room.speech import ActorIdentity
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
    VOTE_CLOSE_EVENT_KIND,
    VOTE_STATE_EVENT_KINDS,
    vote_deadline_has_passed,
    vote_creator_participant_id,
    vote_poll,
    vote_summary,
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
        can_moderate: bool = False,
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
        if kind not in {"vote", *VOTE_STATE_EVENT_KINDS} and not content and not attachments:
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
        actor = ActorIdentity(
            agent_id=participant_id,
            display_name=display_name,
            participant_type=(
                clean_room_text(participant.get("participant_type"), 32)
                or clean_room_text(identity.get("participant_type"), 32)
                or "human"
            ),
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
        if kind in VOTE_STATE_EVENT_KINDS:
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
            vote_events = unit.vote_events(vote_id)
            try:
                closed = bool(vote_summary(vote_events, vote_id).get("closed"))
            except ValueError as error:
                raise RoomCommandRejected(
                    str(error),
                    code="vote_not_found",
                ) from error
            if closed:
                raise RoomCommandRejected(
                    "This vote has ended.",
                    code="vote_closed",
                )
            if kind == VOTE_CLOSE_EVENT_KIND:
                principals = room_identity_principals(identity)
                creator_id = vote_creator_participant_id(poll)
                creator = unit.participant(creator_id)
                creator_owner_id = clean_room_text(
                    creator.get("owner_id") or creator.get("created_by"),
                    128,
                )
                if not (
                    can_moderate
                    or creator_id in principals
                    or (creator_owner_id and creator_owner_id in principals)
                ):
                    raise RoomCommandRejected(
                        "Only the vote creator or a room host can close this vote.",
                        code="permission_denied",
                    )
                vote_choice = None
            elif kind == "vote_cast":
                vote_choice = resolve_vote_choice(
                    payload.get("vote_choice"),
                    list(poll.get("vote_options") or []),
                )
                if not vote_choice:
                    raise RoomCommandRejected(
                        "vote_choice must match one of the vote options.",
                        code="invalid_vote_choice",
                    )
            else:
                vote_choice = None
            vote_question = None
            vote_options = None
            content = ""
        event = unit.append_event(
            "message_final",
            participant_id=participant_id,
            participant_type=actor.participant_type,
            actor_id=participant_id,
            actor_type=actor.actor_type,
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
                None if kind in VOTE_STATE_EVENT_KINDS else payload.get("target_agent_id")
            ),
            relay_depth=0,
        )
        return {"event": event, "event_seq": event["seq"]}

    def edit_in_unit(
        self,
        identity: dict[str, object],
        payload: dict[str, object],
        *,
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        event = self._mutable_message(payload, unit=unit, allowed_kinds={"", "message"})
        participant_id = clean_room_text(identity.get("agent_id"), 128)
        actor_id = self._event_actor_id(event)
        if (
            clean_room_text(identity.get("participant_type"), 32) != "human"
            or clean_room_text(event.get("actor_type"), 32) != "human"
            or actor_id not in room_identity_principals(identity)
        ):
            raise RoomCommandRejected(
                "Only the author can edit this message.",
                code="permission_denied",
            )
        content = room_message_text(payload.get("content"), limit=12000)
        attachments = (
            event.get("attachments")
            if isinstance(event.get("attachments"), list)
            else []
        )
        if not content and not attachments:
            raise RoomCommandRejected(
                "Message content or an attachment is required.",
                code="empty",
            )
        edited_at = utc_now()
        updated = unit.update_event_fields(
            str(event["id"]),
            content=content,
            edited_at=edited_at,
        )
        mutation = unit.append_event(
            "message_updated",
            participant_id=participant_id,
            participant_type="human",
            target_event_id=event["id"],
            target_seq=event["seq"],
            content=content,
            edited_at=edited_at,
        )
        return {"message": updated, "event": mutation, "event_seq": mutation["seq"]}

    def delete_in_unit(
        self,
        identity: dict[str, object],
        payload: dict[str, object],
        *,
        unit: RoomCommandUnitOfWork,
        can_moderate: bool,
    ) -> dict[str, object]:
        event = self._mutable_message(
            payload,
            unit=unit,
            allowed_kinds={"", "message", "vote"},
        )
        actor_id = self._event_actor_id(event)
        principals = room_identity_principals(identity)
        participant = unit.participant(actor_id)
        owned_agent = clean_room_text(
            participant.get("owner_id") or participant.get("created_by"),
            128,
        ) in principals
        own_human_message = (
            clean_room_text(event.get("actor_type"), 32) == "human"
            and actor_id in principals
        )
        if not (can_moderate or owned_agent or own_human_message):
            raise RoomCommandRejected(
                "You cannot delete this message.",
                code="permission_denied",
            )
        message_kind = clean_room_text(event.get("message_kind"), 64)
        vote_events = unit.vote_events(str(event["id"])) if message_kind == "vote" else [event]
        attachment_ids = [
            clean_room_text(item.get("id"), 64)
            for record in vote_events
            for item in (
                record.get("attachments")
                if isinstance(record.get("attachments"), list)
                else []
            )
            if isinstance(item, dict)
            and clean_room_text(item.get("id"), 64)
        ]
        deleted_at = utc_now()
        if message_kind == "vote":
            for ballot in vote_events[1:]:
                unit.update_event_fields(
                    str(ballot["id"]),
                    content="",
                    attachments=[],
                    message_deleted=True,
                    deleted_at=deleted_at,
                    target_agent_id="",
                    vote_id="",
                    vote_choice="",
                    vote_question="",
                    vote_options=[],
                    actor={},
                    actor_id="",
                    actor_type="",
                    participant_id="",
                    display_name="",
                )
        vote_tombstone = (
            {
                "vote_question": "",
                "vote_options": [],
                "vote_duration_seconds": None,
                "vote_deadline_at": "",
            }
            if message_kind == "vote"
            else {}
        )
        updated = unit.update_event_fields(
            str(event["id"]),
            content="",
            attachments=[],
            message_deleted=True,
            deleted_at=deleted_at,
            target_agent_id="",
            **vote_tombstone,
        )
        participant_id = clean_room_text(identity.get("agent_id"), 128)
        mutation = unit.append_event(
            "message_deleted",
            participant_id=participant_id,
            participant_type="human",
            target_event_id=event["id"],
            target_seq=event["seq"],
            deleted_at=deleted_at,
        )
        return {
            "message": updated,
            "event": mutation,
            "event_seq": mutation["seq"],
            "target_event_id": event["id"],
            "attachment_ids": attachment_ids,
        }

    def cleanup_deleted_attachments(self, attachment_ids: object) -> None:
        if not isinstance(attachment_ids, list):
            return
        try:
            for attachment_id in (
                clean_room_text(value, 64) for value in attachment_ids
            ):
                if attachment_id:
                    self._attachments.delete(attachment_id)
        except (AttachmentError, OSError) as error:
            raise RoomCommandRejected(
                "The message was deleted, but an attachment could not be removed.",
                code="message_cleanup_failed",
            ) from error

    @staticmethod
    def _event_actor_id(event: dict[str, object]) -> str:
        actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
        return clean_room_text(
            event.get("participant_id") or event.get("actor_id") or actor.get("participant_id"),
            128,
        )

    @staticmethod
    def _mutable_message(
        payload: dict[str, object],
        *,
        unit: RoomCommandUnitOfWork,
        allowed_kinds: set[str],
    ) -> dict[str, object]:
        event_id = clean_room_text(payload.get("event_id"), 128)
        event = unit.event_by_id(event_id) if event_id else {}
        if not event or event.get("type") != "message_final":
            raise RoomCommandRejected("Message was not found.", code="message_not_found")
        if event.get("message_deleted") is True:
            raise RoomCommandRejected("Message was already deleted.", code="message_deleted")
        if clean_room_text(event.get("message_kind"), 64) not in allowed_kinds:
            raise RoomCommandRejected(
                "This message type cannot be changed here.",
                code="unsupported_message_type",
            )
        return event

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
        if (
            not participant
            or participant.get("status") not in ACTIVE_PARTICIPANT_STATUSES
        ):
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
