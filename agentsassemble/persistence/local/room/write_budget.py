from __future__ import annotations

import sqlite3


def create_room_write_budget_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS room_write_budgets (
               room_id TEXT NOT NULL,
               window_started_at INTEGER NOT NULL,
               command_count INTEGER NOT NULL,
               payload_bytes INTEGER NOT NULL,
               PRIMARY KEY (room_id, window_started_at)
           )"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_room_write_budgets_window
           ON room_write_budgets(window_started_at)"""
    )


def reserve_room_write_budget(
    connection: sqlite3.Connection,
    room_id: str,
    *,
    window_started_at: int,
    command_limit: int,
    payload_byte_limit: int,
    payload_bytes: int,
) -> bool:
    """Atomically consume one room-wide write slot in the active transaction."""

    _validate_limits(command_limit, payload_byte_limit, payload_bytes)
    connection.execute(
        "DELETE FROM room_write_budgets WHERE window_started_at < ?",
        (window_started_at,),
    )
    row = connection.execute(
        """SELECT command_count, payload_bytes FROM room_write_budgets
           WHERE room_id = ? AND window_started_at = ?""",
        (room_id, window_started_at),
    ).fetchone()
    command_count = int(row["command_count"]) if row is not None else 0
    byte_count = int(row["payload_bytes"]) if row is not None else 0
    if command_count + 1 > command_limit or byte_count + payload_bytes > payload_byte_limit:
        return False
    connection.execute(
        """INSERT INTO room_write_budgets(
               room_id, window_started_at, command_count, payload_bytes
           ) VALUES(?, ?, 1, ?)
           ON CONFLICT(room_id, window_started_at) DO UPDATE SET
               command_count = excluded.command_count + room_write_budgets.command_count,
               payload_bytes = excluded.payload_bytes + room_write_budgets.payload_bytes""",
        (room_id, window_started_at, payload_bytes),
    )
    return True


def _validate_limits(command_limit: int, payload_byte_limit: int, payload_bytes: int) -> None:
    if command_limit <= 0 or payload_byte_limit <= 0 or payload_bytes < 0:
        raise ValueError("Room write budget limits must be positive and payload bytes non-negative.")
