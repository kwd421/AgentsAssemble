"""PostgreSQL persistence for identity-owned room preferences."""
from __future__ import annotations

from psycopg import Connection
from psycopg.types.json import Jsonb

from agentsassemble.identity_room_preferences import canonical_user_id
from agentsassemble.room_repository_records import clean_room_id
from agentsassemble.room_user_preferences import (
    RoomUserPreferencesRecord,
    default_room_user_preferences,
    merge_room_user_preferences,
    validate_room_user_preferences,
)


def read_room_preferences(
    connection: Connection,
    user_id: str,
    room_id: str,
) -> RoomUserPreferencesRecord:
    clean_user_id = canonical_user_id(user_id)
    canonical_room_id = clean_room_id(room_id)
    row = connection.execute(
        """SELECT data_json FROM identity_room_user_preferences
           WHERE user_id = %s AND room_id = %s""",
        (clean_user_id, canonical_room_id),
    ).fetchone()
    if row is None:
        return default_room_user_preferences()
    try:
        return validate_room_user_preferences(row["data_json"])
    except ValueError as error:
        raise ValueError(
            f"Stored room preferences are invalid for user {clean_user_id!r} "
            f"in room {canonical_room_id!r}."
        ) from error


def update_room_preferences(
    connection: Connection,
    user_id: str,
    room_id: str,
    updates: dict[str, object],
    *,
    now: str,
) -> RoomUserPreferencesRecord:
    clean_user_id = canonical_user_id(user_id)
    canonical_room_id = clean_room_id(room_id)
    if connection.execute(
        "SELECT 1 FROM identity_users WHERE user_id = %s",
        (clean_user_id,),
    ).fetchone() is None:
        raise ValueError(f"User {clean_user_id!r} was not found.")
    row = connection.execute(
        """SELECT data_json, created_at FROM identity_room_user_preferences
           WHERE user_id = %s AND room_id = %s FOR UPDATE""",
        (clean_user_id, canonical_room_id),
    ).fetchone()
    if row is None:
        current = default_room_user_preferences()
        created_at = now
    else:
        current = validate_room_user_preferences(row["data_json"])
        created_at = str(row["created_at"])
    updated = merge_room_user_preferences(current, updates)
    connection.execute(
        """INSERT INTO identity_room_user_preferences(
               user_id, room_id, data_json, created_at, updated_at
           ) VALUES(%s, %s, %s, %s, %s)
           ON CONFLICT(user_id, room_id) DO UPDATE SET
               data_json = excluded.data_json,
               updated_at = excluded.updated_at""",
        (
            clean_user_id,
            canonical_room_id,
            Jsonb(updated),
            created_at,
            now,
        ),
    )
    return updated
