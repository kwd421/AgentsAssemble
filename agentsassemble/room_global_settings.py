"""Canonical room-global settings shared by every repository backend.

This module owns the durable room-wide record only. User notification/read
preferences and participant roles deliberately live outside this boundary.
Legacy ``room_settings.json`` normalization remains in ``room_settings.py``
until the explicit migration phase.
"""
from __future__ import annotations

from typing import TypedDict

from agentsassemble.room_channels import (
    CHANNEL_NAME_LIMIT,
    CHANNEL_TYPES,
    MAX_CHANNELS_PER_ROOM,
    clean_channel_name,
    is_channel_id,
)
from agentsassemble.room_setting_values import (
    CONVERSATION_MODES,
    IMAGE_URL_LIMIT,
    ROOM_TEXT_LIMIT,
    VALID_BANNER_PRESETS,
    VALID_INVITE_SCOPES,
    clean_room_asset_url,
    clean_room_text,
    clean_short_label,
)


DEFAULT_CONVERSATION_MODE = "ordered"
DEFAULT_MAX_RELAY_TURNS = 6
MIN_RELAY_TURNS = 2
MAX_RELAY_TURNS = 20

ROOM_GLOBAL_SETTING_FIELDS = frozenset(
    {
        "label",
        "topic",
        "appearance",
        "conversation_mode",
        "max_relay_turns",
        "channels",
    }
)
ROOM_APPEARANCE_FIELDS = frozenset(
    {
        "banner_preset",
        "banner_image_url",
        "icon_image_url",
        "icon_label",
        "invite_scope",
    }
)
ROOM_CHANNEL_FIELDS = frozenset({"id", "name", "type", "position", "created_at"})


class RoomGlobalAppearance(TypedDict):
    banner_preset: str
    banner_image_url: str
    icon_image_url: str
    icon_label: str
    invite_scope: str


class RoomGlobalChannel(TypedDict):
    id: str
    name: str
    type: str
    position: int
    created_at: str


class RoomGlobalSettingsRecord(TypedDict):
    label: str
    topic: str
    appearance: RoomGlobalAppearance
    conversation_mode: str
    max_relay_turns: int
    channels: list[RoomGlobalChannel]


def default_room_global_settings(*, label: str = "") -> RoomGlobalSettingsRecord:
    """Return a complete canonical record for a newly created room."""

    return validate_room_global_settings(
        {
            "label": label,
            "topic": "",
            "appearance": {
                "banner_preset": "default",
                "banner_image_url": "",
                "icon_image_url": "",
                "icon_label": "",
                "invite_scope": "room",
            },
            "conversation_mode": DEFAULT_CONVERSATION_MODE,
            "max_relay_turns": DEFAULT_MAX_RELAY_TURNS,
            "channels": [],
        }
    )


def validate_room_global_settings(value: object) -> RoomGlobalSettingsRecord:
    """Validate a complete canonical record without silently repairing it."""

    source = _require_mapping(value, field="room settings")
    _require_exact_fields(source, ROOM_GLOBAL_SETTING_FIELDS, field="room settings")
    return {
        "label": _strict_text(source["label"], field="label", limit=80),
        "topic": _strict_text(source["topic"], field="topic", limit=ROOM_TEXT_LIMIT),
        "appearance": _validate_appearance(source["appearance"]),
        "conversation_mode": _validate_conversation_mode(source["conversation_mode"]),
        "max_relay_turns": _validate_max_relay_turns(source["max_relay_turns"]),
        "channels": _validate_channels(source["channels"]),
    }


def merge_room_global_settings(
    current: object,
    updates: object,
) -> RoomGlobalSettingsRecord:
    """Apply a strict partial update to an already-canonical room record."""

    canonical = validate_room_global_settings(current)
    changes = _require_mapping(updates, field="room settings update")
    unknown = set(changes) - ROOM_GLOBAL_SETTING_FIELDS
    if unknown:
        raise ValueError(f"Unsupported room settings fields: {', '.join(sorted(unknown))}.")
    return validate_room_global_settings({**canonical, **changes})


def _validate_appearance(value: object) -> RoomGlobalAppearance:
    source = _require_mapping(value, field="appearance")
    _require_exact_fields(source, ROOM_APPEARANCE_FIELDS, field="appearance")

    banner_preset = _strict_text(source["banner_preset"], field="banner_preset", limit=24)
    if banner_preset not in VALID_BANNER_PRESETS:
        raise ValueError(f"Unsupported banner_preset: {banner_preset or '<empty>'}.")

    invite_scope = _strict_text(source["invite_scope"], field="invite_scope", limit=24)
    if invite_scope not in VALID_INVITE_SCOPES:
        raise ValueError(f"Unsupported invite_scope: {invite_scope or '<empty>'}.")

    return {
        "banner_preset": banner_preset,
        "banner_image_url": _strict_asset_url(
            source["banner_image_url"],
            field="banner_image_url",
        ),
        "icon_image_url": _strict_asset_url(
            source["icon_image_url"],
            field="icon_image_url",
        ),
        "icon_label": _strict_short_label(source["icon_label"]),
        "invite_scope": invite_scope,
    }


def _validate_conversation_mode(value: object) -> str:
    if not isinstance(value, str) or value not in CONVERSATION_MODES:
        raise ValueError(f"Unsupported conversation_mode: {value!r}.")
    return value


def _validate_max_relay_turns(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("max_relay_turns must be an integer.")
    if not MIN_RELAY_TURNS <= value <= MAX_RELAY_TURNS:
        raise ValueError(
            f"max_relay_turns must be between {MIN_RELAY_TURNS} and {MAX_RELAY_TURNS}."
        )
    return value


def _validate_channels(value: object) -> list[RoomGlobalChannel]:
    if not isinstance(value, list):
        raise ValueError("channels must be a list.")
    if len(value) > MAX_CHANNELS_PER_ROOM:
        raise ValueError(f"channels cannot contain more than {MAX_CHANNELS_PER_ROOM} entries.")

    seen: set[str] = set()
    channels: list[RoomGlobalChannel] = []
    for position, raw_channel in enumerate(value):
        source = _require_mapping(raw_channel, field=f"channels[{position}]")
        _require_exact_fields(source, ROOM_CHANNEL_FIELDS, field=f"channels[{position}]")

        channel_id = _strict_text(source["id"], field=f"channels[{position}].id", limit=13)
        if not is_channel_id(channel_id):
            raise ValueError(f"channels[{position}].id is not a canonical channel id.")
        if channel_id in seen:
            raise ValueError(f"Duplicate channel id: {channel_id}.")
        seen.add(channel_id)

        name = _strict_text(
            source["name"],
            field=f"channels[{position}].name",
            limit=CHANNEL_NAME_LIMIT,
        )
        if not name or clean_channel_name(name) != name:
            raise ValueError(f"channels[{position}].name is not canonical.")

        channel_type = source["type"]
        if not isinstance(channel_type, str) or channel_type not in CHANNEL_TYPES:
            raise ValueError(f"channels[{position}].type is unsupported.")

        stored_position = source["position"]
        if (
            isinstance(stored_position, bool)
            or not isinstance(stored_position, int)
            or stored_position != position
        ):
            raise ValueError("Channel positions must be dense and match list order.")

        channels.append(
            {
                "id": channel_id,
                "name": name,
                "type": channel_type,
                "position": position,
                "created_at": _strict_text(
                    source["created_at"],
                    field=f"channels[{position}].created_at",
                    limit=64,
                ),
            }
        )
    return channels


def _strict_asset_url(value: object, *, field: str) -> str:
    text = _strict_text(value, field=field, limit=IMAGE_URL_LIMIT)
    if clean_room_asset_url(text) != text:
        raise ValueError(f"{field} must be empty or a canonical room attachment URL.")
    return text


def _strict_short_label(value: object) -> str:
    text = _strict_text(value, field="icon_label", limit=2)
    if clean_short_label(text) != text:
        raise ValueError("icon_label is not canonical.")
    return text


def _strict_text(value: object, *, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string.")
    if clean_room_text(value, limit=limit) != value:
        raise ValueError(f"{field} must be canonical single-line text up to {limit} characters.")
    return value


def _require_mapping(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object.")
    return value


def _require_exact_fields(
    source: dict[str, object],
    expected: frozenset[str],
    *,
    field: str,
) -> None:
    missing = expected - set(source)
    unknown = set(source) - expected
    if missing:
        raise ValueError(f"Missing {field} fields: {', '.join(sorted(missing))}.")
    if unknown:
        raise ValueError(f"Unsupported {field} fields: {', '.join(sorted(unknown))}.")
