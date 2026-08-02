"""SQLite ownership for immutable server and room identifiers."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from uuid import uuid4

from agentsassemble.room.text import clean_room_text


def ensure_durable_identity_schema(
    connection: sqlite3.Connection,
    ensure_column: Callable[[sqlite3.Connection, str, str, str], None],
) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS identity_metadata (
               key TEXT PRIMARY KEY,
               value TEXT NOT NULL,
               created_at TEXT NOT NULL DEFAULT ''
           )"""
    )
    ensure_column(connection, "rooms", "room_uid", "TEXT")
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_identity_rooms_uid ON rooms(room_uid)"
    )


def read_or_create_server_id(connection: sqlite3.Connection, *, now: str) -> str:
    row = connection.execute(
        "SELECT value FROM identity_metadata WHERE key = 'server_id'"
    ).fetchone()
    if row is not None:
        return str(row["value"])
    server_id = str(uuid4())
    connection.execute(
        "INSERT INTO identity_metadata(key, value, created_at) VALUES('server_id', ?, ?)",
        (server_id, now),
    )
    return server_id


def upsert_room_identity(
    connection: sqlite3.Connection,
    *,
    room_id: str,
    room_uid: str,
    owner_id: str,
    label: str,
    origin: str,
    now: str,
) -> sqlite3.Row:
    clean_room_id = clean_room_text(room_id, limit=128)
    if not clean_room_id:
        raise ValueError("room_id is required.")
    clean_room_uid = clean_room_text(room_uid, limit=64)
    existing = connection.execute(
        "SELECT * FROM rooms WHERE room_id = ?",
        (clean_room_id,),
    ).fetchone()
    if existing is None:
        connection.execute(
            """INSERT INTO rooms(
                   room_id, room_uid, owner_id, label, created_at,
                   last_active_at, archived, origin
               ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
            (
                clean_room_id,
                clean_room_uid or str(uuid4()),
                clean_room_text(owner_id, limit=128),
                clean_room_text(label, limit=128),
                now,
                now,
                clean_room_text(origin, limit=64),
            ),
        )
    else:
        existing_uid = str(existing["room_uid"] or "")
        if clean_room_uid and existing_uid and clean_room_uid != existing_uid:
            raise ValueError(f"room_uid for {clean_room_id} is immutable.")
        updates: dict[str, object] = {"last_active_at": now}
        for column, value in (
            ("room_uid", clean_room_uid if not existing_uid else ""),
            ("owner_id", clean_room_text(owner_id, limit=128)),
            ("label", clean_room_text(label, limit=128)),
            ("origin", clean_room_text(origin, limit=64)),
        ):
            if value:
                updates[column] = value
        assignments = ", ".join(f"{column} = ?" for column in updates)
        connection.execute(
            f"UPDATE rooms SET {assignments} WHERE room_id = ?",
            (*updates.values(), clean_room_id),
        )
    return connection.execute(
        "SELECT * FROM rooms WHERE room_id = ?",
        (clean_room_id,),
    ).fetchone()


__all__ = [
    "ensure_durable_identity_schema",
    "read_or_create_server_id",
    "upsert_room_identity",
]
