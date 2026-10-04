"""Canonical room vote validation and tallying.

A poll is one room message (message_kind "vote"; its event id is the vote_id)
and any number of vote state changes referencing that vote_id. ``vote_cast``
sets or replaces a choice, ``vote_withdraw`` removes it, and ``vote_close``
ends the poll early. The log is the source of truth — no separate vote store —
and the latest ballot change per voter before closure wins.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agentsassemble.room.text import (
    clean_room_text as clean_lobby_text,
    has_room_visible_text,
)


MIN_VOTE_DURATION_SECONDS = 30
MAX_VOTE_DURATION_SECONDS = 86400
VOTE_QUESTION_LIMIT = 300
VOTE_OPTION_LIMIT = 100
MAX_VOTE_OPTIONS = 10
VOTE_BALLOT_EVENT_KINDS = frozenset({"vote_cast", "vote_withdraw"})
VOTE_CLOSE_EVENT_KIND = "vote_close"
VOTE_STATE_EVENT_KINDS = frozenset({*VOTE_BALLOT_EVENT_KINDS, VOTE_CLOSE_EVENT_KIND})


def normalize_vote_definition(
    question: object,
    options: object,
) -> tuple[str, list[str]]:
    if not isinstance(question, str):
        raise ValueError("vote_question must be text.")
    clean_question = clean_lobby_text(question, limit=VOTE_QUESTION_LIMIT)
    if not clean_question or not has_room_visible_text(clean_question):
        raise ValueError("vote_question is required.")
    if not isinstance(options, list):
        raise ValueError("vote_options must be a list.")
    clean_options: list[str] = []
    seen: set[str] = set()
    for value in options:
        if not isinstance(value, str):
            raise ValueError("Every vote option must be text.")
        option = clean_lobby_text(value, limit=VOTE_OPTION_LIMIT)
        if not option or not has_room_visible_text(option):
            continue
        folded = option.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        clean_options.append(option)
    if len(clean_options) < 2:
        raise ValueError("A vote requires at least two distinct options.")
    if len(clean_options) > MAX_VOTE_OPTIONS:
        raise ValueError(f"A vote supports at most {MAX_VOTE_OPTIONS} options.")
    return clean_question, clean_options


def normalize_vote_duration_seconds(value: object) -> int | None:
    """Return a strict canonical vote duration.

    An omitted value or zero means that the poll has no deadline. Positive
    durations must stay within the product's bounded poll window.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("vote_duration_seconds must be an integer.")
    if value == 0:
        return 0
    if not MIN_VOTE_DURATION_SECONDS <= value <= MAX_VOTE_DURATION_SECONDS:
        raise ValueError(
            "vote_duration_seconds must be 0 or between "
            f"{MIN_VOTE_DURATION_SECONDS} and {MAX_VOTE_DURATION_SECONDS}."
        )
    return value


def deadline_for_vote(
    duration_seconds: int | None,
    *,
    now: datetime | None = None,
) -> str:
    if not duration_seconds:
        return ""
    current = now or datetime.now(UTC)
    return (current + timedelta(seconds=duration_seconds)).isoformat()


def vote_deadline_has_passed(
    deadline_at: object,
    *,
    now: datetime | None = None,
) -> bool:
    clean_deadline = clean_lobby_text(deadline_at, limit=128)
    if not clean_deadline:
        return False
    try:
        deadline = datetime.fromisoformat(clean_deadline.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("vote_deadline_at is invalid.") from error
    if deadline.tzinfo is None:
        raise ValueError("vote_deadline_at must include a timezone.")
    return (now or datetime.now(UTC)) >= deadline


def vote_poll(
    event: dict[str, object],
    vote_id: object,
) -> dict[str, object]:
    """Return a canonical poll event matching ``vote_id`` or raise ValueError."""

    clean_vote_id = clean_lobby_text(vote_id, limit=128)
    if not clean_vote_id:
        raise ValueError("vote_id is required.")
    kind = str(event.get("message_kind") or event.get("kind") or "")
    event_vote_id = str(
        event.get("vote_id")
        or (event.get("id") if kind == "vote" else "")
        or ""
    )
    if (
        kind != "vote"
        or event_vote_id != clean_vote_id
        or event.get("message_deleted") is True
    ):
        raise ValueError(f"Vote {clean_vote_id} was not found.")
    return event


def _participant_id(event: dict[str, object]) -> str:
    actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
    return clean_lobby_text(
        actor.get("participant_id")
        or event.get("participant_id")
        or event.get("actor_id"),
        limit=128,
    )


def vote_creator_participant_id(poll: dict[str, object]) -> str:
    return _participant_id(poll)


def _display_name(event: dict[str, object], participant_id: str) -> str:
    return (
        clean_lobby_text(
            event.get("display_name") or event.get("name"),
            limit=64,
        )
        or participant_id
    )


def vote_summary(
    events: list[dict[str, object]],
    vote_id: str,
    *,
    viewer_participant_id: str = "",
) -> dict[str, object]:
    """Aggregate one poll from chronological canonical vote events.

    Raises ValueError when the poll event is missing from the given events.
    """
    clean_vote_id = clean_lobby_text(vote_id, limit=128)
    if not clean_vote_id:
        raise ValueError("vote_id is required.")
    poll: dict[str, object] | None = None
    latest_choice_by_voter: dict[str, str] = {}
    closed_at = ""
    for event in events:
        if event.get("message_deleted") is True:
            continue
        kind = str(event.get("message_kind") or event.get("kind") or "")
        event_vote_id = str(
            event.get("vote_id")
            or (event.get("id") if kind == "vote" else "")
            or ""
        )
        if event_vote_id != clean_vote_id:
            continue
        if kind == "vote" and poll is None:
            poll = event
            continue
        if kind == VOTE_CLOSE_EVENT_KIND and poll is not None:
            if not closed_at:
                closed_at = clean_lobby_text(event.get("created_at"), limit=128)
            continue
        if kind not in VOTE_BALLOT_EVENT_KINDS or poll is None or closed_at:
            continue
        participant_id = _participant_id(event)
        if not participant_id:
            continue
        if kind == "vote_withdraw":
            latest_choice_by_voter.pop(participant_id, None)
            continue
        choice = str(event.get("vote_choice") or "")
        options = poll.get("vote_options") if isinstance(poll.get("vote_options"), list) else []
        matched = resolve_vote_choice(choice, options)
        if not matched:
            continue
        latest_choice_by_voter[participant_id] = matched
    if poll is None:
        raise ValueError(f"Vote {clean_vote_id} was not found.")

    deadline_closed = vote_deadline_has_passed(poll.get("vote_deadline_at"))
    if deadline_closed and not closed_at:
        closed_at = clean_lobby_text(poll.get("vote_deadline_at"), limit=128)

    options = [str(option) for option in poll.get("vote_options") or []]
    tallies = {option: 0 for option in options}
    for choice in latest_choice_by_voter.values():
        tallies[choice] += 1
    clean_viewer_id = clean_lobby_text(viewer_participant_id, limit=128)
    return {
        "vote_id": clean_vote_id,
        "question": str(poll.get("vote_question") or ""),
        "options": options,
        "vote_duration_seconds": int(poll.get("vote_duration_seconds") or 0),
        "vote_deadline_at": str(poll.get("vote_deadline_at") or ""),
        "created_by": str(poll.get("name") or poll.get("display_name") or ""),
        "created_at": str(poll.get("created_at") or ""),
        "tallies": tallies,
        "own_choice": latest_choice_by_voter.get(clean_viewer_id, ""),
        "total_votes": len(latest_choice_by_voter),
        "closed": bool(closed_at),
        "closed_at": closed_at,
        "close_reason": "deadline" if deadline_closed else ("manual" if closed_at else ""),
    }


def legacy_vote_summary(
    events: list[dict[str, object]],
    vote_id: str,
    *,
    viewer_participant_id: str = "",
) -> dict[str, object]:
    """Preserve retained lobby ballots that predate participant identities.

    Canonical room votes must never use a display name as identity. Retained
    lobby records did not always persist ``actor_id``, so their HTTP adapter
    supplies an explicit compatibility key before using the canonical tally.
    """

    adapted_events: list[dict[str, object]] = []
    for event in events:
        kind = str(event.get("message_kind") or event.get("kind") or "")
        if kind != "vote_cast" or _participant_id(event):
            adapted_events.append(event)
            continue
        display_name = _display_name(event, "")
        if not display_name:
            adapted_events.append(event)
            continue
        adapted_events.append(
            {
                **event,
                "actor_id": f"legacy-name:{display_name.casefold()}",
            }
        )
    return vote_summary(
        adapted_events,
        vote_id,
        viewer_participant_id=viewer_participant_id,
    )


def resolve_vote_choice(choice: object, options: list[object]) -> str:
    """Resolve a ballot to an option: exact text (case-insensitive) or a
    1-based number — AI participants often answer "2" rather than the text."""
    cleaned = clean_lobby_text(choice, limit=200)
    if not cleaned:
        return ""
    folded = cleaned.casefold()
    for option in options:
        clean_option = str(option)
        if clean_option.casefold() == folded:
            return clean_option
    if cleaned.isdigit():
        index = int(cleaned)
        if 1 <= index <= len(options):
            return str(options[index - 1])
    return ""
