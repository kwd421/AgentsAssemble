"""Compatibility exports for the optional room-friends service."""
from agentsassemble.features.social.friends import (
    ROOM_FRIEND_ACTIVE_AGENT_STATUSES,
    ROOM_FRIEND_TYPES,
    ROOM_FRIENDS_FILE,
    delete_room_friend,
    normalize_room_friend_type,
    read_room_friends,
    room_friend_suggestions_from_agents,
    room_friend_type_for_agent,
    room_friends_payload,
    room_friends_with_live_agent_status,
    upsert_room_friend,
)

__all__ = [
    "ROOM_FRIEND_ACTIVE_AGENT_STATUSES",
    "ROOM_FRIEND_TYPES",
    "ROOM_FRIENDS_FILE",
    "delete_room_friend",
    "normalize_room_friend_type",
    "read_room_friends",
    "room_friend_suggestions_from_agents",
    "room_friend_type_for_agent",
    "room_friends_payload",
    "room_friends_with_live_agent_status",
    "upsert_room_friend",
]
