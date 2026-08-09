from __future__ import annotations

from psycopg import Connection


def reserve_room_write_budget(
    connection: Connection,
    room_id: str,
    *,
    window_started_at: int,
    command_limit: int,
    payload_byte_limit: int,
    payload_bytes: int,
) -> bool:
    """Atomically consume one room-wide write slot in the active transaction."""

    if command_limit <= 0 or payload_byte_limit <= 0 or payload_bytes < 0:
        raise ValueError("Room write budget limits must be positive and payload bytes non-negative.")
    if payload_bytes > payload_byte_limit:
        return False
    connection.execute(
        "DELETE FROM room_write_budgets WHERE window_started_at < %s",
        (window_started_at,),
    )
    row = connection.execute(
        """INSERT INTO room_write_budgets(
               room_id, window_started_at, command_count, payload_bytes
           ) VALUES(%s, %s, 1, %s)
           ON CONFLICT(room_id, window_started_at) DO UPDATE SET
               command_count = room_write_budgets.command_count + 1,
               payload_bytes = room_write_budgets.payload_bytes + EXCLUDED.payload_bytes
           WHERE room_write_budgets.command_count + 1 <= %s
             AND room_write_budgets.payload_bytes + EXCLUDED.payload_bytes <= %s
           RETURNING command_count""",
        (
            room_id,
            window_started_at,
            payload_bytes,
            command_limit,
            payload_byte_limit,
        ),
    ).fetchone()
    return row is not None
