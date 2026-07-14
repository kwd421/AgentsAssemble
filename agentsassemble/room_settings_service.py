"""Compose canonical room settings with temporary legacy user preferences."""
from __future__ import annotations

from pathlib import Path

from agentsassemble.room_global_settings import (
    ROOM_APPEARANCE_FIELDS,
    ROOM_GLOBAL_SETTING_FIELDS,
    merge_room_global_settings,
)
from agentsassemble.room_repository import RoomRepository
from agentsassemble.room_repository_records import clean_room_id
from agentsassemble.room_setting_values import clean_short_label
from agentsassemble.room_settings import (
    room_settings_payload as legacy_room_settings_payload,
    update_room_settings as update_legacy_room_settings,
)


_TOP_LEVEL_FIELDS = ROOM_GLOBAL_SETTING_FIELDS | frozenset(
    {
        "room_id",
        "short_label",
        "member_roles",
        "channel_settings",
        "channelSettings",
        "conversationMode",
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


def room_settings_payload(
    repository: RoomRepository,
    output_root: Path,
    *,
    room_id: str = "",
) -> dict[str, object]:
    if room_id:
        return {
            "room_id": room_id,
            "settings": _project_room_settings(repository, output_root, room_id),
        }
    return {
        "rooms": [
            _project_room_settings(repository, output_root, str(room["room_id"]))
            for room in repository.list_rooms(include_archived=True)
        ]
    }


def update_room_settings(
    repository: RoomRepository,
    output_root: Path,
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
    repository.update_room_settings(room_id, global_updates)

    preference_updates = _legacy_preference_updates(payload)
    if len(preference_updates) > 1:
        update_legacy_room_settings(output_root, preference_updates)
    return room_settings_payload(repository, output_root, room_id=room_id)


def _project_room_settings(
    repository: RoomRepository,
    output_root: Path,
    room_id: str,
) -> dict[str, object]:
    global_settings = repository.room_settings(room_id)
    legacy_payload = legacy_room_settings_payload(output_root, room_id=room_id)
    legacy = legacy_payload.get("settings") if isinstance(legacy_payload, dict) else {}
    legacy = legacy if isinstance(legacy, dict) else {}
    legacy_appearance = legacy.get("appearance")
    legacy_appearance = legacy_appearance if isinstance(legacy_appearance, dict) else {}
    return {
        "room_id": room_id,
        **global_settings,
        "short_label": global_settings["appearance"]["icon_label"],
        "appearance": {
            **global_settings["appearance"],
            "notifications": str(legacy_appearance.get("notifications") or "mentions"),
        },
        "member_roles": dict(legacy.get("member_roles") or {}),
        "channel_settings": dict(legacy.get("channel_settings") or {}),
        "created_at": str(legacy.get("created_at") or ""),
        "updated_at": str(legacy.get("updated_at") or ""),
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
    _copy_alias(payload, updates, "max_relay_turns", "maxRelayTurns")
    return updates


def _legacy_preference_updates(payload: dict[str, object]) -> dict[str, object]:
    updates: dict[str, object] = {"room_id": payload["room_id"]}
    appearance = payload.get("appearance")
    if isinstance(appearance, dict) and "notifications" in appearance:
        updates["appearance"] = {"notifications": appearance["notifications"]}
    if "member_roles" in payload:
        updates["member_roles"] = payload["member_roles"]
    if "channel_settings" in payload or "channelSettings" in payload:
        updates["channel_settings"] = (
            payload["channel_settings"]
            if "channel_settings" in payload
            else payload["channelSettings"]
        )
    return updates


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
