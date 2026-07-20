"""Compatibility exports for retained official-turn HTTP routes."""

from agentsassemble.legacy.meeting.http.official_turn import (
    register_legacy_official_turn_routes,
)

__all__ = ["register_legacy_official_turn_routes"]
