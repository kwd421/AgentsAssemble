"""Server command authorization for room-scoped optional tools."""

from __future__ import annotations

from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.tool_modes import room_random_tools_enabled


def require_room_random_tools(
    settings: object,
) -> None:
    tool_mode = settings.get("tool_mode") if isinstance(settings, dict) else None
    if room_random_tools_enabled(tool_mode):
        return
    raise RoomCommandRejected(
        "Random room tools are available only in tabletop mode.",
        code="room_tool_unavailable",
    )


__all__ = ["require_room_random_tools"]
