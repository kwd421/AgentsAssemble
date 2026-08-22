"""Anonymous vote projection for one provider-private RoomPortal."""

from __future__ import annotations

from agentsassemble.room.text import clean_room_text
from agentsassemble.room.votes import resolve_vote_choice


class RoomPortalVoteProjection:
    """Hold identity-linked ballots in memory and publish only safe aggregates."""

    def __init__(self, *, participant_id: str) -> None:
        self.participant_id = participant_id
        self._ballots: dict[str, dict[str, tuple[int, str]]] = {}

    def ingest_ballot(self, event: dict[str, object]) -> None:
        vote_id = clean_room_text(event.get("vote_id"), limit=128)
        participant_id = clean_room_text(
            event.get("participant_id") or event.get("actor_id"),
            limit=128,
        )
        kind = clean_room_text(event.get("message_kind"), limit=64)
        choice = clean_room_text(event.get("vote_choice"), limit=200)
        if (
            not vote_id
            or not participant_id
            or kind not in {"vote_cast", "vote_withdraw"}
            or (kind == "vote_cast" and not choice)
        ):
            return
        seq = _nonnegative_int(event.get("seq"))
        ballots = self._ballots.setdefault(vote_id, {})
        current = ballots.get(participant_id)
        if current is None or seq >= current[0]:
            ballots[participant_id] = (seq, choice if kind == "vote_cast" else "")

    def refresh(self, messages: list[dict[str, object]]) -> None:
        for message in messages:
            if clean_room_text(message.get("message_kind"), limit=64) != "vote":
                continue
            vote_id = clean_room_text(
                message.get("vote_id") or message.get("id"),
                limit=128,
            )
            options = _vote_options(message)
            tallies = {option: 0 for option in options}
            own_choice = ""
            latest_seq = _nonnegative_int(message.get("vote_created_seq"))
            for participant_id, (seq, raw_choice) in self._ballots.get(
                vote_id, {}
            ).items():
                latest_seq = max(latest_seq, seq)
                choice = resolve_vote_choice(raw_choice, options)
                if not choice:
                    continue
                tallies[choice] += 1
                if participant_id == self.participant_id:
                    own_choice = choice
            message["vote_tallies"] = tallies
            message["vote_total_votes"] = sum(tallies.values())
            message["vote_own_choice"] = own_choice
            # A changed anonymous result must cross the observation cursor even
            # though the individual ballot row is intentionally absent.
            message["seq"] = latest_seq

    def retain_for(self, messages: list[dict[str, object]]) -> None:
        retained_vote_ids = {
            clean_room_text(item.get("vote_id") or item.get("id"), limit=128)
            for item in messages
            if clean_room_text(item.get("message_kind"), limit=64) == "vote"
        }
        self._ballots = {
            vote_id: ballots
            for vote_id, ballots in self._ballots.items()
            if vote_id in retained_vote_ids
        }


def render_vote_message(message: dict[str, object]) -> str:
    vote_id = clean_room_text(message.get("vote_id"), limit=128)
    question = clean_room_text(message.get("vote_question"), limit=500)
    options = _vote_options(message)
    lines = [f"[Vote {vote_id}]", question or "(No question provided.)"]
    deadline_at = clean_room_text(message.get("vote_deadline_at"), limit=128)
    if deadline_at:
        lines.append(f"Closes at: {deadline_at}")
    lines.extend(
        f"{index}. {option}" for index, option in enumerate(options, start=1)
    )
    raw_tallies = (
        message.get("vote_tallies")
        if isinstance(message.get("vote_tallies"), dict)
        else {}
    )
    lines.append(
        "Anonymous result: "
        + ", ".join(
            f"{option} {_nonnegative_int(raw_tallies.get(option))}"
            for option in options
        )
    )
    own_choice = resolve_vote_choice(message.get("vote_own_choice"), options)
    if own_choice:
        lines.append(f"Your choice: {own_choice}")
    return "\n".join(lines)


def _vote_options(message: dict[str, object]) -> list[str]:
    values = (
        message.get("vote_options")
        if isinstance(message.get("vote_options"), list)
        else []
    )
    return [
        option
        for value in values
        if (option := clean_room_text(value, limit=200))
    ]


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
