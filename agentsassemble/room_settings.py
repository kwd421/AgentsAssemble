from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

ROOM_TEXT_LIMIT = 160
ROOM_ID_LIMIT = 128
IMAGE_URL_LIMIT = 240
ROLE_ID_LIMIT = 32
MEMBER_ID_LIMIT = 128

VALID_BANNER_PRESETS = {"default", "forest", "midnight", "ember", "custom"}
VALID_NOTIFICATIONS = {"all", "mentions", "mute"}
VALID_INVITE_SCOPES = {"room", "read_only"}
VALID_MEMBER_ROLES = {"human", "director", "implementer", "reviewer", "agent"}
VALID_CHANNEL_IDS = {"lobby", "live", "board", "records"}
VALID_CHANNEL_NOTIFICATIONS = {"default", "all", "mentions", "mute"}


def room_settings_payload(output_root: Path, *, room_id: str = "") -> dict[str, object]:
    state = _read_state(output_root)
    clean_id = clean_room_id(room_id, required=False)
    if clean_id:
        return {
            "room_id": clean_id,
            "settings": public_room_settings(state.get("rooms", {}).get(clean_id), room_id=clean_id),
        }
    rooms = state.get("rooms", {})
    if not isinstance(rooms, dict):
        rooms = {}
    return {
        "rooms": [
            public_room_settings(value, room_id=key)
            for key, value in sorted(rooms.items(), key=lambda item: str(item[0]))
        ]
    }


def update_room_settings(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    room_id = clean_room_id(payload.get("room_id"), required=True)
    state = _read_state(output_root)
    rooms = state.setdefault("rooms", {})
    if not isinstance(rooms, dict):
        rooms = {}
        state["rooms"] = rooms
    current = rooms.get(room_id) if isinstance(rooms.get(room_id), dict) else {}
    settings = public_room_settings(current, room_id=room_id)
    settings["room_id"] = room_id
    if "label" in payload:
        settings["label"] = clean_room_text(payload.get("label"), limit=80)
    if "topic" in payload:
        settings["topic"] = clean_room_text(payload.get("topic"), limit=ROOM_TEXT_LIMIT)
    if "short_label" in payload:
        settings["short_label"] = clean_short_label(payload.get("short_label"))
    if "appearance" in payload:
        settings["appearance"] = clean_appearance(payload.get("appearance"))
    if "member_roles" in payload:
        settings["member_roles"] = clean_member_roles(payload.get("member_roles"))
    if "channel_settings" in payload or "channelSettings" in payload:
        settings["channel_settings"] = clean_channel_settings(
            payload.get("channel_settings") or payload.get("channelSettings")
        )
    settings["updated_at"] = datetime.now(UTC).isoformat()
    if not settings.get("created_at"):
        settings["created_at"] = settings["updated_at"]
    rooms[room_id] = public_room_settings(settings, room_id=room_id)
    _write_state(output_root, state)
    return {"room_id": room_id, "settings": rooms[room_id]}


def public_room_settings(value: object, *, room_id: str) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    clean_id = clean_room_id(source.get("room_id") or room_id, required=True)
    return {
        "room_id": clean_id,
        "label": clean_room_text(source.get("label"), limit=80),
        "topic": clean_room_text(source.get("topic"), limit=ROOM_TEXT_LIMIT),
        "short_label": clean_short_label(source.get("short_label")),
        "appearance": clean_appearance(source.get("appearance")),
        "member_roles": clean_member_roles(source.get("member_roles")),
        "channel_settings": clean_channel_settings(
            source.get("channel_settings") or source.get("channelSettings")
        ),
        "created_at": clean_room_text(source.get("created_at"), limit=64),
        "updated_at": clean_room_text(source.get("updated_at"), limit=64),
    }


def clean_room_id(value: object, *, required: bool) -> str:
    text = clean_room_text(value, limit=ROOM_ID_LIMIT).replace("/", "-").replace("\\", "-")
    if not text and required:
        raise ValueError("room_id is required")
    return text


def clean_room_text(value: object, *, limit: int) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def clean_short_label(value: object) -> str:
    return clean_room_text(value, limit=2).upper()[:2]


def clean_appearance(value: object) -> dict[str, str]:
    source = value if isinstance(value, dict) else {}
    banner_preset = clean_room_text(source.get("banner_preset") or source.get("bannerPreset"), limit=24)
    if banner_preset not in VALID_BANNER_PRESETS:
        banner_preset = "default"
    notifications = clean_room_text(source.get("notifications"), limit=24)
    if notifications not in VALID_NOTIFICATIONS:
        notifications = "mentions"
    invite_scope = clean_room_text(source.get("invite_scope") or source.get("inviteScope"), limit=24)
    if invite_scope not in VALID_INVITE_SCOPES:
        invite_scope = "room"
    return {
        "banner_preset": banner_preset,
        "banner_image_url": clean_room_asset_url(source.get("banner_image_url") or source.get("bannerImage")),
        "icon_image_url": clean_room_asset_url(source.get("icon_image_url") or source.get("iconImage")),
        "icon_label": clean_short_label(source.get("icon_label") or source.get("iconLabel")),
        "notifications": notifications,
        "invite_scope": invite_scope,
    }


def clean_room_asset_url(value: object) -> str:
    text = clean_room_text(value, limit=IMAGE_URL_LIMIT)
    if not text:
        return ""
    if text.startswith("/api/attachments/") and re.fullmatch(r"/api/attachments/[A-Za-z0-9_-]{8,64}\?(view|download)=1", text):
        return text
    return ""


def clean_member_roles(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    roles: dict[str, str] = {}
    for raw_member_id, raw_role in value.items():
        member_id = clean_room_text(raw_member_id, limit=MEMBER_ID_LIMIT)
        role = clean_room_text(raw_role, limit=ROLE_ID_LIMIT)
        if member_id and role in VALID_MEMBER_ROLES:
            roles[member_id] = role
    return roles


def clean_channel_settings(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        return {}
    settings: dict[str, dict[str, str]] = {}
    for raw_channel_id, raw_setting in value.items():
        channel_id = clean_room_text(raw_channel_id, limit=32)
        if channel_id not in VALID_CHANNEL_IDS:
            continue
        source = raw_setting if isinstance(raw_setting, dict) else {}
        notifications = clean_room_text(source.get("notifications"), limit=24)
        if notifications not in VALID_CHANNEL_NOTIFICATIONS:
            notifications = "default"
        last_read_at = clean_room_text(
            source.get("last_read_at") or source.get("lastReadAt"),
            limit=64,
        )
        settings[channel_id] = {
            "notifications": notifications,
            "last_read_at": last_read_at,
        }
    return settings


def _state_path(output_root: Path) -> Path:
    return output_root / "room_settings.json"


def _read_state(output_root: Path) -> dict[str, object]:
    path = _state_path(output_root)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"rooms": {}}
    return state if isinstance(state, dict) else {"rooms": {}}


def _write_state(output_root: Path, state: dict[str, object]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    _state_path(output_root).write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
