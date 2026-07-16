"""Compatibility export for optional room-social HTTP routes."""

from agentsassemble.features.social.routes import (
    register_room_friend_profile_routes,
)


__all__ = ["register_room_friend_profile_routes"]
