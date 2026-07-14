"""SQLite persistence for identity-owned room preferences."""
from __future__ import annotations

import json
import sqlite3

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.room_repository_records import clean_room_id
from agentsassemble.room_user_preferences import (
    RoomUserPreferencesRecord,
    default_room_user_preferences,
    merge_room_user_preferences,
    validate_room_user_preferences,
)


ROOM_PREFERENCE_MIGRATIONS_TABLE = "legacy_room_preference_migrations"

ROOM_PREFERENCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS room_user_preferences (
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    room_id TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, room_id)
);
CREATE INDEX IF NOT EXISTS idx_room_user_preferences_room
    ON room_user_preferences(room_id);

CREATE TABLE IF NOT EXISTS legacy_room_preference_migrations (
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    source_fingerprint TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY (user_id, source_fingerprint)
);
"""


def ensure_room_preferences_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(ROOM_PREFERENCES_SCHEMA)


def read_room_preferences(
    connection: sqlite3.Connection,
    user_id: str,
    room_id: str,
) -> RoomUserPreferencesRecord:
    clean_user_id = canonical_user_id(user_id)
    canonical_room_id = clean_room_id(room_id)
    row = connection.execute(
        "SELECT data_json FROM room_user_preferences"
        " WHERE user_id = ? AND room_id = ?",
        (clean_user_id, canonical_room_id),
    ).fetchone()
    if row is None:
        return default_room_user_preferences()
    try:
        return validate_room_user_preferences(json.loads(str(row["data_json"])))
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError(
            f"Stored room preferences are invalid for user {clean_user_id!r} "
            f"in room {canonical_room_id!r}."
        ) from error


def update_room_preferences(
    connection: sqlite3.Connection,
    user_id: str,
    room_id: str,
    updates: dict[str, object],
    *,
    now: str,
) -> RoomUserPreferencesRecord:
    clean_user_id = canonical_user_id(user_id)
    canonical_room_id = clean_room_id(room_id)
    if connection.execute(
        "SELECT 1 FROM users WHERE user_id = ?",
        (clean_user_id,),
    ).fetchone() is None:
        raise ValueError(f"User {clean_user_id!r} was not found.")
    row = connection.execute(
        "SELECT data_json, created_at FROM room_user_preferences"
        " WHERE user_id = ? AND room_id = ?",
        (clean_user_id, canonical_room_id),
    ).fetchone()
    if row is None:
        current = default_room_user_preferences()
        created_at = now
    else:
        current = validate_room_user_preferences(json.loads(str(row["data_json"])))
        created_at = str(row["created_at"])
    updated = merge_room_user_preferences(current, updates)
    connection.execute(
        "INSERT INTO room_user_preferences"
        " (user_id, room_id, data_json, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(user_id, room_id) DO UPDATE SET"
        " data_json = excluded.data_json, updated_at = excluded.updated_at",
        (
            clean_user_id,
            canonical_room_id,
            encode_room_preferences(updated),
            created_at,
            now,
        ),
    )
    return updated


def delete_room_preferences(connection: sqlite3.Connection, room_id: str) -> None:
    connection.execute(
        "DELETE FROM room_user_preferences WHERE room_id = ?",
        (clean_room_id(room_id),),
    )


def canonical_user_id(value: object) -> str:
    raw = str(value or "")
    cleaned = clean_lobby_text(raw, limit=128)
    if not cleaned or cleaned != raw:
        raise ValueError("user_id is required and must be canonical.")
    return cleaned


def encode_room_preferences(value: object) -> str:
    canonical = validate_room_user_preferences(value)
    return json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
