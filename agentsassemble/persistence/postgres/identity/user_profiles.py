"""PostgreSQL persistence for server user profiles."""
from __future__ import annotations

from psycopg import Connection
from psycopg.types.json import Jsonb

from agentsassemble.identity.preferences import canonical_user_id


def read_user_profile(
    connection: Connection,
    user_id: str,
) -> dict[str, object] | None:
    clean_user_id = canonical_user_id(user_id)
    row = connection.execute(
        "SELECT data_json FROM identity_user_profiles WHERE user_id = %s",
        (clean_user_id,),
    ).fetchone()
    if row is None:
        return None
    profile = row["data_json"]
    if not isinstance(profile, dict):
        raise ValueError(f"Stored user profile is invalid for user {clean_user_id!r}.")
    return dict(profile)


def update_user_profile(
    connection: Connection,
    user_id: str,
    profile: dict[str, object],
    *,
    now: str,
) -> dict[str, object]:
    clean_user_id = canonical_user_id(user_id)
    user = connection.execute(
        "SELECT created_at FROM identity_users WHERE user_id = %s",
        (clean_user_id,),
    ).fetchone()
    if user is None:
        raise ValueError(f"User {clean_user_id!r} was not found.")
    existing = connection.execute(
        "SELECT created_at FROM identity_user_profiles WHERE user_id = %s FOR UPDATE",
        (clean_user_id,),
    ).fetchone()
    created_at = str(existing["created_at"] if existing else user["created_at"] or now)
    stored = {**profile, "created_at": created_at, "updated_at": now}
    connection.execute(
        """INSERT INTO identity_user_profiles(user_id, data_json, created_at, updated_at)
           VALUES(%s, %s, %s, %s)
           ON CONFLICT(user_id) DO UPDATE SET
               data_json = excluded.data_json, updated_at = excluded.updated_at""",
        (clean_user_id, Jsonb(stored), created_at, now),
    )
    connection.execute(
        """UPDATE identity_users SET display_name = %s, avatar_image_url = %s
           WHERE user_id = %s""",
        (
            str(stored.get("display_name") or ""),
            str(stored.get("avatar_image_url") or ""),
            clean_user_id,
        ),
    )
    return stored


__all__ = ["read_user_profile", "update_user_profile"]
