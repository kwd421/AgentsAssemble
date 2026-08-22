"""Backend-neutral validation for canonical room-event payload updates."""

from __future__ import annotations


def apply_event_updates(
    event: dict[str, object],
    updates: dict[str, object],
) -> dict[str, object]:
    immutable = {"id", "seq", "room_id", "type", "created_at"}
    if immutable.intersection(updates):
        raise ValueError("Canonical room event identity fields cannot be changed")
    if "actor" in updates:
        ballot_redaction = (
            event.get("message_kind") in {"vote_cast", "vote_withdraw", "vote_close"}
            and updates.get("message_deleted") is True
            and updates.get("actor") == {}
        )
        if not ballot_redaction:
            raise ValueError("Canonical room event actor cannot be changed")
    return {**event, **updates}


__all__ = ["apply_event_updates"]
