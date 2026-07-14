"""Strictly inspect legacy room_settings.json room-global fields."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentsassemble.room_global_settings import (
    default_room_global_settings,
    merge_room_global_settings,
)
from agentsassemble.room_repository_records import clean_room_id
from agentsassemble.room_setting_values import clean_short_label


LEGACY_ROOM_SETTINGS_SOURCE_VERSION = 1

_ROOM_GLOBAL_SOURCE_FIELDS = frozenset(
    {
        "label",
        "topic",
        "short_label",
        "appearance",
        "conversation_mode",
        "conversationMode",
        "max_relay_turns",
        "maxRelayTurns",
        "channels",
    }
)
_ROOM_PREFERENCE_OR_META_FIELDS = frozenset(
    {
        "room_id",
        "member_roles",
        "channel_settings",
        "channelSettings",
        "created_at",
        "updated_at",
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


class LegacyRoomSettingsSourceError(ValueError):
    """The legacy settings source cannot produce a safe migration plan."""


@dataclass(frozen=True)
class LegacyRoomSettingsSource:
    path: Path
    raw_bytes: bytes
    file_fingerprint: str
    fingerprint: str
    room_count: int
    candidate_room_count: int
    candidate_room_ids: tuple[str, ...]
    preference_only_room_count: int
    updates_by_room: dict[str, dict[str, object]]
    issues: tuple[dict[str, str], ...]


def read_legacy_room_settings_source(path: Path) -> LegacyRoomSettingsSource:
    try:
        raw_bytes = path.read_bytes()
        payload = json.loads(raw_bytes.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise LegacyRoomSettingsSourceError(
            "Legacy room_settings.json is not valid UTF-8."
        ) from error
    except json.JSONDecodeError as error:
        raise LegacyRoomSettingsSourceError(
            f"Legacy room_settings.json is malformed at line {error.lineno}, column {error.colno}."
        ) from error
    except OSError as error:
        raise LegacyRoomSettingsSourceError(
            f"Legacy room_settings.json could not be read: {type(error).__name__}."
        ) from error
    if not isinstance(payload, dict):
        raise LegacyRoomSettingsSourceError("Legacy room_settings.json must contain an object.")
    rooms = payload.get("rooms")
    if not isinstance(rooms, dict):
        raise LegacyRoomSettingsSourceError(
            "Legacy room_settings.json must contain a rooms object."
        )

    issues: list[dict[str, str]] = []
    unknown_top_level = set(payload) - {"rooms"}
    for field in sorted(unknown_top_level):
        issues.append(
            _issue(
                "",
                field,
                "unknown_top_level_field",
                "Remove the unsupported field.",
            )
        )

    projection: dict[str, object] = {
        "version": LEGACY_ROOM_SETTINGS_SOURCE_VERSION,
        "unknown_top_level": {
            field: payload[field] for field in sorted(unknown_top_level)
        },
        "rooms": {},
    }
    updates_by_room: dict[str, dict[str, object]] = {}
    preference_only = 0
    projected_rooms = projection["rooms"]
    assert isinstance(projected_rooms, dict)
    for raw_room_id, raw_settings in sorted(rooms.items(), key=lambda item: str(item[0])):
        room_id = str(raw_room_id)
        room_projection = _global_projection(raw_settings)
        if not room_projection:
            preference_only += 1
            continue
        projected_rooms[room_id] = room_projection
        updates, room_issues = _extract_global_updates(room_id, raw_settings)
        issues.extend(room_issues)
        if not room_issues:
            updates_by_room[room_id] = updates

    serialized = canonical_json(projection).encode("utf-8")
    return LegacyRoomSettingsSource(
        path=path,
        raw_bytes=raw_bytes,
        file_fingerprint=hashlib.sha256(raw_bytes).hexdigest(),
        fingerprint=hashlib.sha256(serialized).hexdigest(),
        room_count=len(rooms),
        candidate_room_count=len(projected_rooms),
        candidate_room_ids=tuple(projected_rooms),
        preference_only_room_count=preference_only,
        updates_by_room=updates_by_room,
        issues=tuple(issues),
    )


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _global_projection(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"invalid_room_record": value}
    projection = {
        field: value[field]
        for field in sorted(_ROOM_GLOBAL_SOURCE_FIELDS - {"appearance"})
        if field in value
    }
    if "appearance" in value:
        appearance = value["appearance"]
        if isinstance(appearance, dict):
            projected_appearance = {
                field: appearance[field]
                for field in sorted(set(appearance) - _APPEARANCE_PREFERENCE_FIELDS)
            }
            if projected_appearance:
                projection["appearance"] = projected_appearance
        else:
            projection["appearance"] = appearance
    unknown = set(value) - _ROOM_GLOBAL_SOURCE_FIELDS - _ROOM_PREFERENCE_OR_META_FIELDS
    if unknown:
        projection["unknown_fields"] = {field: value[field] for field in sorted(unknown)}
    if projection and "room_id" in value:
        projection["room_id"] = value["room_id"]
    return projection


def _extract_global_updates(
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
    unknown = set(value) - _ROOM_GLOBAL_SOURCE_FIELDS - _ROOM_PREFERENCE_OR_META_FIELDS
    for field in sorted(unknown):
        issues.append(
            _issue(
                room_id,
                field,
                "unknown_room_field",
                "Remove the unsupported field.",
            )
        )

    updates: dict[str, object] = {}
    for field in ("label", "topic", "channels"):
        if field in value:
            updates[field] = value[field]
    _copy_alias(value, updates, "conversation_mode", "conversationMode", room_id, issues)
    _copy_alias(value, updates, "max_relay_turns", "maxRelayTurns", room_id, issues)

    appearance_updates = _appearance_updates(room_id, value, issues)
    if appearance_updates:
        updates["appearance"] = appearance_updates

    valid_updates: dict[str, object] = {}
    baseline = default_room_global_settings()
    for field, field_value in updates.items():
        try:
            merge_room_global_settings(baseline, {field: field_value})
        except ValueError as error:
            issues.append(_issue(room_id, field, f"invalid_{field}", str(error)))
        else:
            valid_updates[field] = field_value
    return valid_updates, issues


def _appearance_updates(
    room_id: str,
    source: dict[str, object],
    issues: list[dict[str, str]],
) -> dict[str, object]:
    updates: dict[str, object] = {}
    if "appearance" in source:
        appearance = source["appearance"]
        if not isinstance(appearance, dict):
            issues.append(
                _issue(room_id, "appearance", "invalid_appearance", "Replace it with an object.")
            )
        else:
            unknown = (
                set(appearance)
                - set(_APPEARANCE_ALIASES)
                - _APPEARANCE_PREFERENCE_FIELDS
            )
            for field in sorted(unknown):
                issues.append(
                    _issue(
                        room_id,
                        f"appearance.{field}",
                        "unknown_appearance_field",
                        "Remove the unsupported field.",
                    )
                )
            for source_field, target_field in _APPEARANCE_ALIASES.items():
                if source_field not in appearance:
                    continue
                value = appearance[source_field]
                if target_field in updates and updates[target_field] != value:
                    issues.append(
                        _issue(
                            room_id,
                            f"appearance.{target_field}",
                            "appearance_alias_conflict",
                            "Keep one canonical value for the aliases.",
                        )
                    )
                else:
                    updates[target_field] = value

    if "short_label" in source:
        short_label = source["short_label"]
        if not isinstance(short_label, str) or clean_short_label(short_label) != short_label:
            issues.append(
                _issue(
                    room_id,
                    "short_label",
                    "invalid_short_label",
                    "Use 0-2 canonical uppercase characters.",
                )
            )
        else:
            icon_label = updates.get("icon_label")
            if icon_label not in {None, "", short_label} and short_label:
                issues.append(
                    _issue(
                        room_id,
                        "short_label",
                        "icon_label_conflict",
                        "Make short_label and appearance.icon_label agree.",
                    )
                )
            elif icon_label in {None, ""}:
                updates["icon_label"] = short_label
    return updates


def _copy_alias(
    source: dict[str, object],
    target: dict[str, object],
    canonical: str,
    alias: str,
    room_id: str,
    issues: list[dict[str, str]],
) -> None:
    if canonical in source and alias in source and source[canonical] != source[alias]:
        issues.append(
            _issue(
                room_id,
                canonical,
                "alias_conflict",
                "Keep one value for the canonical field and alias.",
            )
        )
        return
    if canonical in source:
        target[canonical] = source[canonical]
    elif alias in source:
        target[canonical] = source[alias]


def _issue(room_id: str, field: str, code: str, message: str) -> dict[str, str]:
    return {"room_id": room_id, "field": field, "code": code, "message": message}
