"""SQLite persistence for server user profiles."""
from __future__ import annotations

import json
import sqlite3

from agentsassemble.identity.preferences import canonical_user_id


USER_PROFILES_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def ensure_user_profiles_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(USER_PROFILES_SCHEMA)


def read_user_profile(
    connection: sqlite3.Connection,
    user_id: str,
) -> dict[str, object] | None:
    clean_user_id = canonical_user_id(user_id)
    row = connection.execute(
        "SELECT data_json FROM user_profiles WHERE user_id = ?",
        (clean_user_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        profile = json.loads(str(row["data_json"]))
    except json.JSONDecodeError as error:
        raise ValueError(f"Stored user profile is invalid for user {clean_user_id!r}.") from error
    if not isinstance(profile, dict):
        raise ValueError(f"Stored user profile is invalid for user {clean_user_id!r}.")
    return profile


def update_user_profile(
    connection: sqlite3.Connection,
    user_id: str,
    profile: dict[str, object],
    *,
    now: str,
) -> dict[str, object]:
    clean_user_id = canonical_user_id(user_id)
    user = connection.execute(
        "SELECT created_at FROM users WHERE user_id = ?",
        (clean_user_id,),
    ).fetchone()
    if user is None:
        raise ValueError(f"User {clean_user_id!r} was not found.")
    existing = connection.execute(
        "SELECT created_at FROM user_profiles WHERE user_id = ?",
        (clean_user_id,),
    ).fetchone()
    created_at = str(existing["created_at"] if existing else user["created_at"] or now)
    stored = {**profile, "created_at": created_at, "updated_at": now}
    encoded = json.dumps(
        stored,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    connection.execute(
        "INSERT INTO user_profiles(user_id, data_json, created_at, updated_at)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT(user_id) DO UPDATE SET"
        " data_json = excluded.data_json, updated_at = excluded.updated_at",
        (clean_user_id, encoded, created_at, now),
    )
    connection.execute(
        "UPDATE users SET display_name = ?, avatar_image_url = ? WHERE user_id = ?",
        (
            str(stored.get("display_name") or ""),
            str(stored.get("avatar_image_url") or ""),
            clean_user_id,
        ),
    )
    return stored


__all__ = [
    "ensure_user_profiles_schema",
    "read_user_profile",
    "update_user_profile",
]
