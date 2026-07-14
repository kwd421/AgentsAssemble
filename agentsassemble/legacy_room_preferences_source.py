"""Strictly inspect user preference fields in legacy room_settings.json."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.legacy_room_settings_document import (
    read_legacy_room_settings_document,
)
from agentsassemble.room_repository_records import clean_room_id
from agentsassemble.room_user_preferences import (
    CHANNEL_NOTIFICATION_VALUES,
    ROOM_NOTIFICATION_VALUES,
    default_room_user_preferences,
    merge_room_user_preferences,
)


LEGACY_ROOM_PREFERENCES_SOURCE_VERSION = 1


@dataclass(frozen=True)
class LegacyRoomPreferencesSource:
    path: Path
    raw_bytes: bytes
    file_fingerprint: str
    fingerprint: str
    room_count: int
    candidate_room_count: int
    candidate_room_ids: tuple[str, ...]
    updates_by_room: dict[str, dict[str, object]]
    issues: tuple[dict[str, str], ...]


def read_legacy_room_preferences_source(path: Path) -> LegacyRoomPreferencesSource:
    document = read_legacy_room_settings_document(path)
    projection: dict[str, object] = {
        "version": LEGACY_ROOM_PREFERENCES_SOURCE_VERSION,
        "rooms": {},
    }
    projected_rooms = projection["rooms"]
    assert isinstance(projected_rooms, dict)
    updates_by_room: dict[str, dict[str, object]] = {}
    issues: list[dict[str, str]] = []
    for raw_room_id, raw_settings in sorted(
        document.rooms.items(),
        key=lambda item: str(item[0]),
    ):
        room_id = str(raw_room_id)
        preference_projection = _preference_projection(raw_settings)
        if not preference_projection:
            continue
        projected_rooms[room_id] = preference_projection
        updates, room_issues = _extract_preference_updates(room_id, raw_settings)
        issues.extend(room_issues)
        if not room_issues:
            updates_by_room[room_id] = updates

    serialized = _canonical_json(projection).encode("utf-8")
    return LegacyRoomPreferencesSource(
        path=path,
        raw_bytes=document.raw_bytes,
        file_fingerprint=hashlib.sha256(document.raw_bytes).hexdigest(),
        fingerprint=hashlib.sha256(serialized).hexdigest(),
        room_count=len(document.rooms),
        candidate_room_count=len(projected_rooms),
        candidate_room_ids=tuple(projected_rooms),
        updates_by_room=updates_by_room,
        issues=tuple(issues),
    )


def _preference_projection(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"invalid_room_record": value}
    projection: dict[str, object] = {}
    if "appearance" in value:
        appearance = value["appearance"]
        if not isinstance(appearance, dict):
            projection["appearance"] = appearance
        elif "notifications" in appearance:
            projection["appearance"] = {"notifications": appearance["notifications"]}
    for field in ("channel_settings", "channelSettings"):
        if field in value:
            projection[field] = value[field]
    if projection and "room_id" in value:
        projection["room_id"] = value["room_id"]
    return projection


def _extract_preference_updates(
    room_id: str,
    value: object,
) -> tuple[dict[str, object], list[dict[str, str]]]:
    issues: list[dict[str, str]] = []
    if not isinstance(value, dict):
        return {}, [_issue(room_id, "", "invalid_room_record", "Replace it with an object.")]
    try:
        if clean_room_id(room_id) != room_id:
            raise ValueError("room id is not canonical")
    except ValueError:
        issues.append(_issue(room_id, "room_id", "invalid_room_id", "Use a canonical room id."))
    stored_room_id = value.get("room_id")
    if stored_room_id is not None and stored_room_id != room_id:
        issues.append(
            _issue(
                room_id,
                "room_id",
                "room_id_mismatch",
                "Match the containing room key exactly.",
            )
        )

    updates: dict[str, object] = {}
    appearance = value.get("appearance")
    if "appearance" in value and not isinstance(appearance, dict):
        issues.append(
            _issue(
                room_id,
                "appearance",
                "invalid_preference_appearance",
                "Replace it with an object before migrating preferences.",
            )
        )
    elif isinstance(appearance, dict) and "notifications" in appearance:
        notification = appearance["notifications"]
        if not isinstance(notification, str) or notification not in ROOM_NOTIFICATION_VALUES:
            issues.append(
                _issue(
                    room_id,
                    "appearance.notifications",
                    "invalid_notifications",
                    f"Use one of: {', '.join(sorted(ROOM_NOTIFICATION_VALUES))}.",
                )
            )
        else:
            updates["notifications"] = notification

    snake_settings = value.get("channel_settings")
    camel_settings = value.get("channelSettings")
    if (
        "channel_settings" in value
        and "channelSettings" in value
        and snake_settings != camel_settings
    ):
        issues.append(
            _issue(
                room_id,
                "channel_settings",
                "channel_settings_alias_conflict",
                "Keep one value for channel_settings and channelSettings.",
            )
        )
    elif "channel_settings" in value or "channelSettings" in value:
        raw_settings = snake_settings if "channel_settings" in value else camel_settings
        channel_settings, channel_issues = _legacy_channel_preferences(
            room_id,
            raw_settings,
        )
        issues.extend(channel_issues)
        if not channel_issues:
            updates["channel_settings"] = channel_settings

    try:
        merge_room_user_preferences(default_room_user_preferences(), updates)
    except ValueError as error:
        issues.append(_issue(room_id, "", "invalid_preferences", str(error)))
    return updates, issues


def _legacy_channel_preferences(
    room_id: str,
    value: object,
) -> tuple[dict[str, dict[str, object]], list[dict[str, str]]]:
    if not isinstance(value, dict):
        return {}, [
            _issue(
                room_id,
                "channel_settings",
                "invalid_channel_settings",
                "Replace it with an object.",
            )
        ]
    output: dict[str, dict[str, object]] = {}
    issues: list[dict[str, str]] = []
    for raw_channel_id, raw_setting in value.items():
        channel_id = str(raw_channel_id)
        field = f"channel_settings.{channel_id}"
        if not isinstance(raw_setting, dict):
            issues.append(
                _issue(
                    room_id,
                    field,
                    "invalid_channel_preference",
                    "Replace it with an object.",
                )
            )
            continue
        notification = raw_setting.get("notifications", "default")
        if not isinstance(notification, str) or notification not in CHANNEL_NOTIFICATION_VALUES:
            issues.append(
                _issue(
                    room_id,
                    f"{field}.notifications",
                    "invalid_channel_notifications",
                    f"Use one of: {', '.join(sorted(CHANNEL_NOTIFICATION_VALUES))}.",
                )
            )
            continue
        snake_cursor = raw_setting.get("last_read_at")
        camel_cursor = raw_setting.get("lastReadAt")
        if (
            "last_read_at" in raw_setting
            and "lastReadAt" in raw_setting
            and snake_cursor != camel_cursor
        ):
            issues.append(
                _issue(
                    room_id,
                    f"{field}.last_read_at",
                    "read_cursor_alias_conflict",
                    "Keep one read cursor value.",
                )
            )
            continue
        cursor = snake_cursor if "last_read_at" in raw_setting else camel_cursor
        output[channel_id] = {
            "notifications": notification,
            "last_read_at": "" if cursor is None else cursor,
        }
    return output, issues


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _issue(room_id: str, field: str, code: str, message: str) -> dict[str, str]:
    return {"room_id": room_id, "field": field, "code": code, "message": message}
