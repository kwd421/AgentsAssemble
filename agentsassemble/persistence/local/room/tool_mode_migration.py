"""SQLite settings migration for canonical room tool availability."""

from __future__ import annotations

import json
import sqlite3


class RoomDatabaseMigrationError(RuntimeError):
    """Existing room state cannot be upgraded without guessing or loss."""


def add_room_tool_mode_setting(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT room_id, data_json FROM room_settings"
    ).fetchall()
    for row in rows:
        try:
            settings = json.loads(str(row["data_json"] or ""))
        except (json.JSONDecodeError, ValueError) as error:
            raise RoomDatabaseMigrationError(
                f"Room settings for {row['room_id']} are unreadable."
            ) from error
        if not isinstance(settings, dict):
            raise RoomDatabaseMigrationError(
                f"Room settings for {row['room_id']} are invalid."
            )
        if "tool_mode" in settings:
            if settings["tool_mode"] not in {"chat", "tabletop"}:
                raise RoomDatabaseMigrationError(
                    f"tool_mode is invalid for room {row['room_id']}."
                )
            continue
        settings["tool_mode"] = "chat"
        connection.execute(
            "UPDATE room_settings SET data_json = ? WHERE room_id = ?",
            (
                json.dumps(
                    settings,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                str(row["room_id"]),
            ),
        )


__all__ = ["RoomDatabaseMigrationError", "add_room_tool_mode_setting"]
