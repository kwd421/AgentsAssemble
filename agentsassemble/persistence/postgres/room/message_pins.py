"""PostgreSQL persistence for channel-scoped pinned message pointers."""
from __future__ import annotations

from agentsassemble.room.repository_records import clean_room_id, utc_now
from agentsassemble.room.text import clean_room_text


def _pin_key(value: object, field: str) -> str:
    clean = clean_room_text(value, limit=128)
    if not clean:
        raise ValueError(f"{field} is required.")
    return clean


class PostgresMessagePinRepositoryMixin:
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
        with self._connection() as connection, connection.transaction():
            if connection.execute(
                "SELECT 1 FROM rooms WHERE room_id = %s", (clean_room,)
            ).fetchone() is None:
                raise ValueError(f"Room {clean_room} was not found.")
            connection.execute(
                """INSERT INTO room_message_pins(
                       room_id, channel_id, event_id, pinned_by, pinned_at
                   ) VALUES(%s, %s, %s, %s, %s)
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
        with self._connection() as connection, connection.transaction():
            return bool(
                connection.execute(
                    """DELETE FROM room_message_pins
                       WHERE room_id = %s AND channel_id = %s AND event_id = %s""",
                    (clean_room, clean_channel, clean_event),
                ).rowcount
            )

    def pinned_messages(self, room_id: str, channel_id: str) -> list[dict[str, object]]:
        clean_room = clean_room_id(room_id)
        clean_channel = _pin_key(channel_id, "channel_id")
        with self._read_connection() as connection:
            rows = connection.execute(
                """SELECT room_id, channel_id, event_id, pinned_by, pinned_at
                   FROM room_message_pins
                   WHERE room_id = %s AND channel_id = %s
                   ORDER BY pinned_at DESC, event_id ASC""",
                (clean_room, clean_channel),
            ).fetchall()
        return [
            {
                "room_id": str(row.get("room_id") or ""),
                "channel_id": str(row.get("channel_id") or ""),
                "event_id": str(row.get("event_id") or ""),
                "pinned_by": str(row.get("pinned_by") or ""),
                "pinned_at": str(row.get("pinned_at") or ""),
            }
            for row in rows
        ]


__all__ = ["PostgresMessagePinRepositoryMixin"]
