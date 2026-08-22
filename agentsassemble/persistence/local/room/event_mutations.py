"""SQLite reads and in-place payload mutations for canonical room events."""

from __future__ import annotations

import json
import sqlite3

from agentsassemble.persistence.local.room.database import VISIBLE
from agentsassemble.room.text import clean_room_text


def _event_payload(row: sqlite3.Row | None) -> dict[str, object]:
    if row is None:
        return {}
    try:
        payload = json.loads(str(row["payload_json"]))
    except (json.JSONDecodeError, ValueError, IndexError, KeyError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def read_event_by_id(
    connection: sqlite3.Connection,
    room_id: str,
    event_id: str,
    *,
    include_hidden: bool = False,
) -> dict[str, object]:
    clean_event_id = clean_room_text(event_id, limit=128)
    if not clean_event_id:
        return {}
    query = "SELECT payload_json FROM room_events WHERE room_id = ? AND event_id = ?"
    parameters: tuple[object, ...] = (room_id, clean_event_id)
    if not include_hidden:
        query += " AND visibility = ?"
        parameters = (room_id, clean_event_id, VISIBLE)
    return _event_payload(connection.execute(query, parameters).fetchone())


def update_event_fields(
    connection: sqlite3.Connection,
    room_id: str,
    event_id: str,
    updates: dict[str, object],
) -> dict[str, object]:
    event = read_event_by_id(connection, room_id, event_id)
    if not event:
        raise ValueError(f"Room event was not found: {event_id}")
    immutable = {"id", "seq", "room_id", "type", "created_at", "actor"}
    if immutable.intersection(updates):
        raise ValueError("Canonical room event identity fields cannot be changed")
    updated = {**event, **updates}
    connection.execute(
        "UPDATE room_events SET payload_json = ? WHERE room_id = ? AND event_id = ?",
        (
            json.dumps(
                updated,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            room_id,
            str(event["id"]),
        ),
    )
    return updated


__all__ = ["read_event_by_id", "update_event_fields"]
