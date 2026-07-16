"""Strict user-owned notification and read preferences for one room."""
from __future__ import annotations

from typing import TypedDict

from agentsassemble.room.channels import MAX_CHANNELS_PER_ROOM, is_channel_id


ROOM_NOTIFICATION_VALUES = frozenset({"all", "mentions", "mute"})
CHANNEL_NOTIFICATION_VALUES = frozenset({"default", "all", "mentions", "mute"})
BUILTIN_CHANNEL_IDS = frozenset({"lobby", "live", "board", "records"})
MAX_PREFERENCE_CHANNELS = MAX_CHANNELS_PER_ROOM + len(BUILTIN_CHANNEL_IDS)
READ_CURSOR_LIMIT = 64


class ChannelPreference(TypedDict):
    notifications: str
    last_read_at: str


class RoomUserPreferencesRecord(TypedDict):
    notifications: str
    channel_settings: dict[str, ChannelPreference]


def default_room_user_preferences() -> RoomUserPreferencesRecord:
    return {
        "notifications": "mentions",
        "channel_settings": {},
    }


def validate_room_user_preferences(value: object) -> RoomUserPreferencesRecord:
    source = _require_mapping(value, field="room user preferences")
    _require_exact_fields(
        source,
        frozenset({"notifications", "channel_settings"}),
        field="room user preferences",
    )
    return {
        "notifications": _notification_value(source["notifications"]),
        "channel_settings": _channel_settings(source["channel_settings"]),
    }


def merge_room_user_preferences(
    current: object,
    updates: object,
) -> RoomUserPreferencesRecord:
    canonical = validate_room_user_preferences(current)
    changes = _require_mapping(updates, field="room user preferences update")
    unknown = set(changes) - {"notifications", "channel_settings"}
    if unknown:
        raise ValueError(
            "Unsupported room user preference fields: "
            f"{', '.join(sorted(unknown))}."
        )
    return validate_room_user_preferences({**canonical, **changes})


def _notification_value(value: object) -> str:
    if not isinstance(value, str) or value not in ROOM_NOTIFICATION_VALUES:
        raise ValueError(f"Unsupported room notification mode: {value!r}.")
    return value


def _channel_settings(value: object) -> dict[str, ChannelPreference]:
    source = _require_mapping(value, field="channel_settings")
    if len(source) > MAX_PREFERENCE_CHANNELS:
        raise ValueError(
            f"channel_settings cannot contain more than {MAX_PREFERENCE_CHANNELS} entries."
        )
    output: dict[str, ChannelPreference] = {}
    for channel_id, raw_setting in source.items():
        if not isinstance(channel_id, str) or not _is_supported_channel_id(channel_id):
            raise ValueError(f"Unsupported preference channel id: {channel_id!r}.")
        setting = _require_mapping(
            raw_setting,
            field=f"channel_settings.{channel_id}",
        )
        _require_exact_fields(
            setting,
            frozenset({"notifications", "last_read_at"}),
            field=f"channel_settings.{channel_id}",
        )
        notification = setting["notifications"]
        if not isinstance(notification, str) or notification not in CHANNEL_NOTIFICATION_VALUES:
            raise ValueError(
                f"Unsupported channel notification mode for {channel_id}: {notification!r}."
            )
        output[channel_id] = {
            "notifications": notification,
            "last_read_at": _read_cursor(setting["last_read_at"], channel_id=channel_id),
        }
    return output


def _is_supported_channel_id(channel_id: str) -> bool:
    return channel_id in BUILTIN_CHANNEL_IDS or is_channel_id(channel_id)


def _read_cursor(value: object, *, channel_id: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Read cursor for {channel_id} must be a string.")
    if len(value) > READ_CURSOR_LIMIT or any(character in value for character in "\r\n\t"):
        raise ValueError(f"Read cursor for {channel_id} is not canonical.")
    return value


def _require_mapping(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object with string keys.")
    return dict(value)


def _require_exact_fields(
    value: dict[str, object],
    expected: frozenset[str],
    *,
    field: str,
) -> None:
    missing = expected - set(value)
    unknown = set(value) - expected
    if missing:
        raise ValueError(f"{field} is missing fields: {', '.join(sorted(missing))}.")
    if unknown:
        raise ValueError(f"{field} has unsupported fields: {', '.join(sorted(unknown))}.")
