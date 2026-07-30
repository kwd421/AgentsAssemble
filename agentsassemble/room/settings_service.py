"""Compose repository-owned room settings with identity-owned preferences."""
from __future__ import annotations

from agentsassemble.identity.repository import IdentityBackend
from agentsassemble.room.global_settings import (
    ROOM_APPEARANCE_FIELDS,
    ROOM_GLOBAL_SETTING_FIELDS,
    merge_room_global_settings,
    public_room_global_settings,
)
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.repository_records import clean_room_id
from agentsassemble.room.setting_values import clean_short_label
from agentsassemble.room.user_preferences import (
    default_room_user_preferences,
    merge_room_user_preferences,
)


_TOP_LEVEL_FIELDS = ROOM_GLOBAL_SETTING_FIELDS | frozenset(
    {
        "room_id",
        "short_label",
        "channel_settings",
        "channelSettings",
        "conversationMode",
        "orderedExcludePreviousSpeaker",
        "maxRelayTurns",
    }
)
_APPEARANCE_ALIASES = {
    "banner_preset": "banner_preset",
    "bannerPreset": "banner_preset",
    "banner_image_url": "banner_image_url",
    "bannerImage": "banner_image_url",
    "icon_image_url": "icon_image_url",
    "iconImage": "icon_image_url",
    "icon_label": "icon_label",
    "iconLabel": "icon_label",
    "invite_scope": "invite_scope",
    "inviteScope": "invite_scope",
}
_APPEARANCE_PREFERENCE_FIELDS = frozenset({"notifications"})
_GLOBAL_TOP_LEVEL_FIELDS = frozenset(
    {
        "label",
        "topic",
        "channels",
        "short_label",
        "conversation_mode",
        "conversationMode",
        "ordered_exclude_previous_speaker",
        "orderedExcludePreviousSpeaker",
        "max_relay_turns",
        "maxRelayTurns",
    }
)


def room_settings_payload(
    repository: RoomRepository,
    identities: IdentityBackend,
    *,
    user_id: str,
    room_id: str = "",
) -> dict[str, object]:
    if room_id:
        return {
            "room_id": room_id,
            "settings": _project_room_settings(
                repository,
                identities,
                user_id=user_id,
                room_id=room_id,
            ),
        }
    return {
        "rooms": [
            _project_room_settings(
                repository,
                identities,
                user_id=user_id,
                room_id=str(room["room_id"]),
            )
            for room in repository.list_rooms(include_archived=True)
        ]
    }


def update_room_settings(
    repository: RoomRepository,
    identities: IdentityBackend,
    *,
    user_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    raw_room_id = str(payload.get("room_id") or "").strip()
    if not raw_room_id:
        raise ValueError("room_id is required")
    room_id = clean_room_id(raw_room_id)
    unknown = set(payload) - _TOP_LEVEL_FIELDS
    if unknown:
        raise ValueError(f"Unsupported room settings fields: {', '.join(sorted(unknown))}.")

    current = repository.room_settings(room_id)
    global_updates = _global_updates(payload)
    merge_room_global_settings(current, global_updates)

    preference_updates = _preference_updates(payload)
    current_preferences = (
        identities.room_preferences(user_id, room_id)
        if user_id
        else default_room_user_preferences()
    )
    merge_room_user_preferences(current_preferences, preference_updates)
    if preference_updates and not user_id:
        raise ValueError("A stable user identity is required to save room preferences.")
    if global_updates and preference_updates:
        raise ValueError(
            "Room-global settings and user preferences must be saved in separate requests."
        )

    if global_updates:
        repository.update_room_settings(room_id, global_updates)
    if preference_updates:
        identities.update_room_preferences(user_id, room_id, preference_updates)
    return room_settings_payload(
        repository,
        identities,
        user_id=user_id,
        room_id=room_id,
    )


def has_room_global_updates(payload: dict[str, object]) -> bool:
    """Return whether an HTTP settings payload attempts a room-wide write."""

    if set(payload) & _GLOBAL_TOP_LEVEL_FIELDS:
        return True
    appearance = payload.get("appearance")
    return isinstance(appearance, dict) and bool(
        set(appearance) - _APPEARANCE_PREFERENCE_FIELDS
    )


def _project_room_settings(
    repository: RoomRepository,
    identities: IdentityBackend,
    *,
    user_id: str,
    room_id: str,
) -> dict[str, object]:
    global_settings = public_room_global_settings(
        repository.room_settings(room_id)
    )
    preferences = (
        identities.room_preferences(user_id, room_id)
        if user_id
        else default_room_user_preferences()
    )
    return {
        "room_id": room_id,
        **global_settings,
        "short_label": global_settings["appearance"]["icon_label"],
        "appearance": {
            **global_settings["appearance"],
            "notifications": preferences["notifications"],
        },
        "channel_settings": preferences["channel_settings"],
    }


def _global_updates(payload: dict[str, object]) -> dict[str, object]:
    updates: dict[str, object] = {}
    for field in ("label", "topic", "channels"):
        if field in payload:
            updates[field] = payload[field]

    appearance = payload.get("appearance")
    if "appearance" in payload:
        if not isinstance(appearance, dict):
            raise ValueError("appearance must be an object.")
        appearance_updates: dict[str, object] = {}
        seen_aliases: dict[str, object] = {}
        for source_field, value in appearance.items():
            if source_field in _APPEARANCE_PREFERENCE_FIELDS:
                continue
            target_field = _APPEARANCE_ALIASES.get(source_field)
            if target_field is None or target_field not in ROOM_APPEARANCE_FIELDS:
                raise ValueError(f"Unsupported appearance fields: {source_field}.")
            if target_field in seen_aliases and seen_aliases[target_field] != value:
                raise ValueError(f"Conflicting appearance field aliases for {target_field}.")
            seen_aliases[target_field] = value
            appearance_updates[target_field] = value
        if appearance_updates:
            updates["appearance"] = appearance_updates

    short_label = payload.get("short_label")
    if "short_label" in payload:
        if not isinstance(short_label, str) or clean_short_label(short_label) != short_label:
            raise ValueError("short_label is not canonical.")
        clean_label = clean_short_label(short_label)
        existing_icon = (
            updates.get("appearance", {}).get("icon_label")
            if isinstance(updates.get("appearance"), dict)
            else None
        )
        if existing_icon is None:
            updates["appearance"] = {
                **(
                    updates.get("appearance")
                    if isinstance(updates.get("appearance"), dict)
                    else {}
                ),
                "icon_label": clean_label,
            }

    _copy_alias(payload, updates, "conversation_mode", "conversationMode")
    _copy_alias(
        payload,
        updates,
        "ordered_exclude_previous_speaker",
        "orderedExcludePreviousSpeaker",
    )
    _copy_alias(payload, updates, "max_relay_turns", "maxRelayTurns")
    return updates


def _preference_updates(payload: dict[str, object]) -> dict[str, object]:
    updates: dict[str, object] = {}
    appearance = payload.get("appearance")
    if isinstance(appearance, dict) and "notifications" in appearance:
        updates["notifications"] = appearance["notifications"]
    if "channel_settings" in payload or "channelSettings" in payload:
        if (
            "channel_settings" in payload
            and "channelSettings" in payload
            and payload["channel_settings"] != payload["channelSettings"]
        ):
            raise ValueError("Conflicting room settings aliases for channel_settings.")
        raw_settings = (
            payload["channel_settings"]
            if "channel_settings" in payload
            else payload["channelSettings"]
        )
        updates["channel_settings"] = _canonical_channel_preferences(raw_settings)
    return updates


def _canonical_channel_preferences(value: object) -> dict[str, dict[str, object]]:
    if not isinstance(value, dict):
        raise ValueError("channel_settings must be an object.")
    output: dict[str, dict[str, object]] = {}
    for channel_id, raw_setting in value.items():
        if not isinstance(raw_setting, dict):
            raise ValueError(f"channel_settings.{channel_id} must be an object.")
        unknown = set(raw_setting) - {"notifications", "last_read_at", "lastReadAt"}
        if unknown:
            raise ValueError(
                f"Unsupported channel preference fields for {channel_id}: "
                f"{', '.join(sorted(unknown))}."
            )
        if (
            "last_read_at" in raw_setting
            and "lastReadAt" in raw_setting
            and raw_setting["last_read_at"] != raw_setting["lastReadAt"]
        ):
            raise ValueError(f"Conflicting read cursor aliases for {channel_id}.")
        cursor = (
            raw_setting["last_read_at"]
            if "last_read_at" in raw_setting
            else raw_setting.get("lastReadAt", "")
        )
        output[str(channel_id)] = {
            "notifications": raw_setting.get("notifications"),
            "last_read_at": cursor,
        }
    return output


def _copy_alias(
    source: dict[str, object],
    target: dict[str, object],
    canonical: str,
    alias: str,
) -> None:
    if canonical in source and alias in source and source[canonical] != source[alias]:
        raise ValueError(f"Conflicting room settings aliases for {canonical}.")
    if canonical in source:
        target[canonical] = source[canonical]
    elif alias in source:
        target[canonical] = source[alias]
