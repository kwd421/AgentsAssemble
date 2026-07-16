"""Compatibility exports for canonical room-global settings."""

from agentsassemble.room.global_settings import (
    DEFAULT_CONVERSATION_MODE,
    DEFAULT_MAX_RELAY_TURNS,
    MAX_RELAY_TURNS,
    MIN_RELAY_TURNS,
    ROOM_APPEARANCE_FIELDS,
    ROOM_CHANNEL_FIELDS,
    ROOM_GLOBAL_SETTING_FIELDS,
    ROOM_LABEL_LIMIT,
    RoomGlobalAppearance,
    RoomGlobalChannel,
    RoomGlobalSettingsRecord,
    default_room_global_settings,
    merge_room_global_settings,
    validate_room_global_settings,
)


__all__ = [
    "DEFAULT_CONVERSATION_MODE",
    "DEFAULT_MAX_RELAY_TURNS",
    "MAX_RELAY_TURNS",
    "MIN_RELAY_TURNS",
    "ROOM_APPEARANCE_FIELDS",
    "ROOM_CHANNEL_FIELDS",
    "ROOM_GLOBAL_SETTING_FIELDS",
    "ROOM_LABEL_LIMIT",
    "RoomGlobalAppearance",
    "RoomGlobalChannel",
    "RoomGlobalSettingsRecord",
    "default_room_global_settings",
    "merge_room_global_settings",
    "validate_room_global_settings",
]
