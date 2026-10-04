"""SQLite persistence for channel-scoped pinned message pointers."""
from __future__ import annotations

import sqlite3

from agentsassemble.room.repository_records import clean_room_id, utc_now
from agentsassemble.room.text import clean_room_text


def create_message_pin_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS room_message_pins (
               room_id TEXT NOT NULL,
               channel_id TEXT NOT NULL,
               event_id TEXT NOT NULL,
               pinned_by TEXT NOT NULL,
               pinned_at TEXT NOT NULL,
               PRIMARY KEY (room_id, channel_id, event_id),
               FOREIGN KEY (room_id) REFERENCES rooms(room_id) ON DELETE CASCADE
           )"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_message_pins_channel
           ON room_message_pins(room_id, channel_id, pinned_at DESC)"""
    )


def _pin_key(value: object, field: str) -> str:
    clean = clean_room_text(value, limit=128)
    if not clean:
        raise ValueError(f"{field} is required.")
    return clean


class SQLiteMessagePinRepositoryMixin:
    def pin_message(
        self,
        room_id: str,
        channel_id: str,
        event_id: str,
        *,
        pinned_by: str,
    ) -> dict[str, object]:
        clean_room = clean_room_id(room_id)
        clean_channel = _pin_key(channel_id, "channel_id")
        clean_event = _pin_key(event_id, "event_id")
        clean_actor = _pin_key(pinned_by, "pinned_by")
        pinned_at = utc_now()
        with self._lock, self._write_transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM rooms WHERE room_id = ?", (clean_room,)
            ).fetchone() is None:
                raise ValueError(f"Room {clean_room} was not found.")
            connection.execute(
                """INSERT INTO room_message_pins(
                       room_id, channel_id, event_id, pinned_by, pinned_at
                   ) VALUES(?, ?, ?, ?, ?)
                   ON CONFLICT(room_id, channel_id, event_id) DO UPDATE SET
                       pinned_by = excluded.pinned_by,
                       pinned_at = excluded.pinned_at""",
                (clean_room, clean_channel, clean_event, clean_actor, pinned_at),
            )
        return {
            "room_id": clean_room,
            "channel_id": clean_channel,
            "event_id": clean_event,
            "pinned_by": clean_actor,
            "pinned_at": pinned_at,
        }

    def unpin_message(self, room_id: str, channel_id: str, event_id: str) -> bool:
        clean_room = clean_room_id(room_id)
        clean_channel = _pin_key(channel_id, "channel_id")
        clean_event = _pin_key(event_id, "event_id")
        with self._lock, self._write_transaction() as connection:
            return bool(
                connection.execute(
                    """DELETE FROM room_message_pins
                       WHERE room_id = ? AND channel_id = ? AND event_id = ?""",
                    (clean_room, clean_channel, clean_event),
                ).rowcount
            )

    def pinned_messages(self, room_id: str, channel_id: str) -> list[dict[str, object]]:
        clean_room = clean_room_id(room_id)
        clean_channel = _pin_key(channel_id, "channel_id")
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT room_id, channel_id, event_id, pinned_by, pinned_at
                   FROM room_message_pins
                   WHERE room_id = ? AND channel_id = ?
                   ORDER BY pinned_at DESC, event_id ASC""",
                (clean_room, clean_channel),
            ).fetchall()
        return [dict(row) for row in rows]


__all__ = ["SQLiteMessagePinRepositoryMixin", "create_message_pin_schema"]
