"""Compatibility exports for retained resident room-session HTTP routes."""

from agentsassemble.legacy.live_agent.http.room_session import (
    register_legacy_room_session_route,
)

__all__ = ["register_legacy_room_session_route"]
