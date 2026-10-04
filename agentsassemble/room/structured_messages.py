"""Validation and canonical event fields for structured room publications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentsassemble.room.text import clean_room_text, has_room_visible_text
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


class RoomEventReader(Protocol):
    def event_by_id(self, event_id: str) -> dict[str, object]: ...

    def vote_events(self, vote_id: str) -> list[dict[str, object]]: ...


class StructuredMessageError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StructuredRoomMessage:
    content: str
    message_kind: str = "message"
    vote_id: str = ""
    vote_question: str = ""
    vote_options: tuple[str, ...] = ()
    vote_duration_seconds: int = 0
    vote_choice: str = ""


def prepare_structured_message(payload: dict[str, object]) -> StructuredRoomMessage:
    message_kind = _clean_text(payload.get("kind"), limit=64) or "message"
    if message_kind not in {"message", "vote", *VOTE_STATE_EVENT_KINDS}:
        raise StructuredMessageError(
            "Provider final message kind is unsupported.",
            code="invalid_message_kind",
        )
    content = room_message_text(payload.get("content"), limit=12_000)
    if message_kind == "message":
        if not has_room_visible_text(content):
            raise StructuredMessageError(
                "Provider final message was empty.",
                code="empty_provider_final",
            )
        return StructuredRoomMessage(content=content)
    if message_kind == "vote":
        try:
            question, options = normalize_vote_definition(
                payload.get("vote_question"),
                payload.get("vote_options"),
            )
            duration = normalize_vote_duration_seconds(
                payload.get("vote_duration_seconds")
            )
        except ValueError as error:
            raise StructuredMessageError(str(error), code="invalid_vote") from error
        return StructuredRoomMessage(
            content="",
            message_kind="vote",
            vote_question=question,
            vote_options=tuple(options),
            vote_duration_seconds=duration or 0,
        )
    vote_id = _clean_text(payload.get("vote_id"), limit=128)
    vote_choice = _clean_text(payload.get("vote_choice"), limit=100)
    if not vote_id or (message_kind == "vote_cast" and not vote_choice):
        raise StructuredMessageError(
            "A vote id and choice are required.",
            code="invalid_vote_choice",
        )
    return StructuredRoomMessage(
        content="",
        message_kind=message_kind,
        vote_id=vote_id,
        vote_choice=vote_choice,
    )


def canonical_structured_fields(
    message: StructuredRoomMessage,
    *,
    events: RoomEventReader,
    participant_id: str = "",
) -> dict[str, object]:
    fields: dict[str, object] = {
        "message_kind": message.message_kind,
        "vote_id": message.vote_id or None,
        "vote_question": message.vote_question or None,
        "vote_options": list(message.vote_options) if message.vote_options else None,
        "vote_duration_seconds": None,
        "vote_deadline_at": "",
        "vote_choice": message.vote_choice or None,
    }
    if message.message_kind == "vote":
        fields.update(
            vote_id=None,
            vote_duration_seconds=message.vote_duration_seconds,
            vote_deadline_at=deadline_for_vote(message.vote_duration_seconds),
            vote_choice=None,
        )
        return fields
    if message.message_kind not in VOTE_STATE_EVENT_KINDS:
        return fields
    try:
        poll = vote_poll(events.event_by_id(message.vote_id), message.vote_id)
    except ValueError as error:
        raise StructuredMessageError(str(error), code="vote_not_found") from error
    if vote_deadline_has_passed(poll.get("vote_deadline_at")):
        raise StructuredMessageError("This vote has ended.", code="vote_expired")
    if vote_summary(events.vote_events(message.vote_id), message.vote_id).get("closed"):
        raise StructuredMessageError("This vote has ended.", code="vote_closed")
    if (
        message.message_kind == VOTE_CLOSE_EVENT_KIND
        and vote_creator_participant_id(poll) != clean_room_text(participant_id, limit=128)
    ):
        raise StructuredMessageError(
            "Only the vote creator can close this vote from an Agent Session.",
            code="permission_denied",
        )
    choice = ""
    if message.message_kind == "vote_cast":
        choice = resolve_vote_choice(message.vote_choice, list(poll.get("vote_options") or []))
        if not choice:
            raise StructuredMessageError(
                "vote_choice must match one of the vote options.",
                code="invalid_vote_choice",
            )
    fields.update(vote_question=None, vote_options=None, vote_choice=choice)
    return fields


def room_message_text(value: object, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return (
        value.replace("\x00", "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip()[:limit]
    )


def _clean_text(value: object, *, limit: int) -> str:
    return clean_room_text(value, limit=limit)


__all__ = [
    "StructuredMessageError",
    "StructuredRoomMessage",
    "canonical_structured_fields",
    "prepare_structured_message",
    "room_message_text",
]
