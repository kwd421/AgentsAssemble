"""Poll tallying over lobby events (issue 7: /vote).

A poll is one lobby event (kind "vote": question + options; its event id is the
vote_id) and any number of ballots (kind "vote_cast" referencing that vote_id).
The log is the source of truth — no separate vote store — and the latest cast
per voter wins, so re-voting just changes your choice.
"""
from __future__ import annotations

from agentsassemble.room.text import clean_room_text as clean_lobby_text


def vote_summary(events: list[dict[str, object]], vote_id: str) -> dict[str, object]:
    """Aggregate one poll from (chronological) lobby events.

    Raises ValueError when the poll event is missing from the given events.
    """
    clean_vote_id = clean_lobby_text(vote_id, limit=128)
    if not clean_vote_id:
        raise ValueError("vote_id is required.")
    poll: dict[str, object] | None = None
    latest_choice_by_voter: dict[str, tuple[str, str]] = {}  # voter key -> (choice, display name)
    for event in events:
        kind = str(event.get("kind") or event.get("message_kind") or "")
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
        if kind != "vote_cast" or poll is None:
            continue
        choice = str(event.get("vote_choice") or "")
        options = poll.get("vote_options") if isinstance(poll.get("vote_options"), list) else []
        matched = _match_option(choice, [str(option) for option in options])
        if not matched:
            continue
        voter_key = str(event.get("actor_id") or "") or f"name:{str(event.get('name') or '')}"
        if voter_key in {"", "name:"}:
            continue
        latest_choice_by_voter[voter_key] = (
            matched,
            str(event.get("name") or event.get("display_name") or voter_key),
        )
    if poll is None:
        raise ValueError(f"Vote {clean_vote_id} was not found.")

    options = [str(option) for option in poll.get("vote_options") or []]
    tallies = {option: 0 for option in options}
    voters: dict[str, list[str]] = {option: [] for option in options}
    for choice, display_name in latest_choice_by_voter.values():
        tallies[choice] += 1
        voters[choice].append(display_name)
    return {
        "vote_id": clean_vote_id,
        "question": str(poll.get("vote_question") or ""),
        "options": options,
        "created_by": str(poll.get("name") or poll.get("display_name") or ""),
        "created_at": str(poll.get("created_at") or ""),
        "tallies": tallies,
        "voters": voters,
        "total_votes": len(latest_choice_by_voter),
    }


def _match_option(choice: str, options: list[str]) -> str:
    """Resolve a ballot to an option: exact text (case-insensitive) or a
    1-based number — AI participants often answer "2" rather than the text."""
    cleaned = clean_lobby_text(choice, limit=200)
    if not cleaned:
        return ""
    folded = cleaned.casefold()
    for option in options:
        if option.casefold() == folded:
            return option
    if cleaned.isdigit():
        index = int(cleaned)
        if 1 <= index <= len(options):
            return options[index - 1]
    return ""
