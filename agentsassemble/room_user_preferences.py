"""Compatibility exports for room user notification and read preferences."""

from agentsassemble.room.user_preferences import (
    BUILTIN_CHANNEL_IDS,
    CHANNEL_NOTIFICATION_VALUES,
    MAX_PREFERENCE_CHANNELS,
    READ_CURSOR_LIMIT,
    ROOM_NOTIFICATION_VALUES,
    ChannelPreference,
    RoomUserPreferencesRecord,
    default_room_user_preferences,
    merge_room_user_preferences,
    validate_room_user_preferences,
)


__all__ = [
    "BUILTIN_CHANNEL_IDS",
    "CHANNEL_NOTIFICATION_VALUES",
    "MAX_PREFERENCE_CHANNELS",
    "READ_CURSOR_LIMIT",
    "ROOM_NOTIFICATION_VALUES",
    "ChannelPreference",
    "RoomUserPreferencesRecord",
    "default_room_user_preferences",
    "merge_room_user_preferences",
    "validate_room_user_preferences",
]
