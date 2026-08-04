"""Authenticated user-profile normalization and room synchronization."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from agentsassemble.identity.repository import (
    IdentityBackend,
    LOCAL_OPERATOR_DISPLAY_NAME_DEFAULT,
    LOCAL_OPERATOR_USER_ID,
)
from agentsassemble.room.repository import RoomRepository

PROFILE_TEXT_LIMIT = 120
CUSTOM_STATUS_LIMIT = 160
AVATAR_LABEL_LIMIT = 2
IMAGE_URL_LIMIT = 240
VALID_STATUSES = {"online", "idle", "dnd", "offline"}
VALID_BANNER_PRESETS = {"default", "forest", "midnight", "ember", "custom"}
DEFAULT_PROFILE = {
    "display_name": LOCAL_OPERATOR_DISPLAY_NAME_DEFAULT,
    "handle": "seinel.",
    "status": "online",
    "custom_status": "AgentsAssemble",
    "avatar_label": "나",
    "avatar_image_url": "",
    "banner_preset": "default",
    "accent_color": "#5865f2",
    "mic_muted": True,
    "deafened": False,
}


def read_user_profile(
    output_root: Path,
    *,
    identities: IdentityBackend,
    user_id: str,
) -> dict[str, object]:
    user = identities.get_user(user_id)
    if user is None:
        raise ValueError(f"User {user_id!r} was not found.")
    stored = identities.user_profile(user_id)
    source = stored
    if source is None and user_id == LOCAL_OPERATOR_USER_ID:
        source = _read_legacy_state(output_root).get("profile")
    profile = public_user_profile(source)
    display_name = clean_profile_text(user.get("display_name"), limit=64)
    if display_name:
        profile["display_name"] = display_name
    if stored is not None:
        profile["avatar_image_url"] = clean_avatar_image_url(user.get("avatar_image_url"))
    else:
        avatar_image_url = clean_avatar_image_url(user.get("avatar_image_url"))
        if avatar_image_url:
            profile["avatar_image_url"] = avatar_image_url
        if user_id != LOCAL_OPERATOR_USER_ID:
            profile["avatar_label"] = clean_avatar_label(display_name[:2])
            profile["handle"] = display_name
            profile["custom_status"] = ""
    return {"profile": profile}


def update_user_profile(
    output_root: Path,
    payload: dict[str, object],
    *,
    identities: IdentityBackend,
    rooms: RoomRepository,
    user_id: str,
) -> dict[str, object]:
    current = read_user_profile(
        output_root,
        identities=identities,
        user_id=user_id,
    )["profile"]
    profile = {
        **public_user_profile(current),
        **_clean_profile_update(payload),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if not profile.get("created_at"):
        profile["created_at"] = profile["updated_at"]
    saved = identities.update_user_profile(user_id, public_user_profile(profile))
    public = public_user_profile(saved)
    user = identities.get_user(user_id) or {}
    _synchronize_room_participant_profile(
        identities=identities,
        rooms=rooms,
        participant_id=str(user.get("participant_id") or ""),
        display_name=str(public.get("display_name") or ""),
        avatar_image_url=str(public.get("avatar_image_url") or ""),
    )
    return {"profile": public}


def public_user_profile(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    profile = {
        "display_name": clean_profile_text(source.get("display_name") or DEFAULT_PROFILE["display_name"]),
        "handle": clean_profile_text(source.get("handle") or DEFAULT_PROFILE["handle"]),
        "status": clean_status(source.get("status")),
        "custom_status": clean_profile_text(
            source.get("custom_status") or DEFAULT_PROFILE["custom_status"],
            limit=CUSTOM_STATUS_LIMIT,
        ),
        "avatar_label": clean_avatar_label(source.get("avatar_label") or DEFAULT_PROFILE["avatar_label"]),
        "avatar_image_url": clean_avatar_image_url(source.get("avatar_image_url")),
        "banner_preset": clean_banner_preset(source.get("banner_preset")),
        "accent_color": clean_accent_color(source.get("accent_color")),
        "mic_muted": bool(source.get("mic_muted", DEFAULT_PROFILE["mic_muted"])),
        "deafened": bool(source.get("deafened", DEFAULT_PROFILE["deafened"])),
        "created_at": clean_profile_text(source.get("created_at"), limit=64),
        "updated_at": clean_profile_text(source.get("updated_at"), limit=64),
    }
    return profile


def _clean_profile_update(payload: dict[str, object]) -> dict[str, object]:
    update: dict[str, object] = {}
    if "display_name" in payload:
        update["display_name"] = clean_profile_text(payload.get("display_name") or DEFAULT_PROFILE["display_name"])
    if "handle" in payload:
        update["handle"] = clean_profile_text(payload.get("handle") or DEFAULT_PROFILE["handle"])
    if "status" in payload:
        update["status"] = clean_status(payload.get("status"))
    if "custom_status" in payload:
        update["custom_status"] = clean_profile_text(payload.get("custom_status"), limit=CUSTOM_STATUS_LIMIT)
    if "avatar_label" in payload:
        update["avatar_label"] = clean_avatar_label(payload.get("avatar_label"))
    if "avatar_image_url" in payload:
        update["avatar_image_url"] = clean_avatar_image_url(payload.get("avatar_image_url"))
    if "banner_preset" in payload:
        update["banner_preset"] = clean_banner_preset(payload.get("banner_preset"))
    if "accent_color" in payload:
        update["accent_color"] = clean_accent_color(payload.get("accent_color"))
    if "mic_muted" in payload:
        update["mic_muted"] = bool(payload.get("mic_muted"))
    if "deafened" in payload:
        update["deafened"] = bool(payload.get("deafened"))
    return update


def clean_profile_text(value: object, *, limit: int = PROFILE_TEXT_LIMIT) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def clean_avatar_label(value: object) -> str:
    text = clean_profile_text(value, limit=AVATAR_LABEL_LIMIT).upper()
    return text or str(DEFAULT_PROFILE["avatar_label"])


def clean_avatar_image_url(value: object) -> str:
    text = clean_profile_text(value, limit=IMAGE_URL_LIMIT)
    if not text:
        return ""
    if text.startswith("/api/attachments/") and re.fullmatch(
        r"/api/attachments/[A-Za-z0-9_-]{8,64}\?view=1",
        text,
    ):
        return text
    return ""


def clean_status(value: object) -> str:
    text = clean_profile_text(value, limit=24)
    return text if text in VALID_STATUSES else str(DEFAULT_PROFILE["status"])


def clean_banner_preset(value: object) -> str:
    text = clean_profile_text(value, limit=24)
    return text if text in VALID_BANNER_PRESETS else str(DEFAULT_PROFILE["banner_preset"])


def clean_accent_color(value: object) -> str:
    text = clean_profile_text(value, limit=16)
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", text):
        return text.lower()
    return str(DEFAULT_PROFILE["accent_color"])


def _legacy_state_path(output_root: Path) -> Path:
    return output_root / "user_profile.json"


def _read_legacy_state(output_root: Path) -> dict[str, object]:
    path = _legacy_state_path(output_root)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"profile": DEFAULT_PROFILE}
    return state if isinstance(state, dict) else {"profile": DEFAULT_PROFILE}


def _synchronize_room_participant_profile(
    *,
    identities: IdentityBackend,
    rooms: RoomRepository,
    participant_id: str,
    display_name: str,
    avatar_image_url: str,
) -> None:
    if not participant_id:
        return
    room_ids: set[str] = set()
    for membership in identities.list_memberships():
        if str(membership.get("participant_id") or "") != participant_id:
            continue
        room_id = str(membership.get("meeting_id") or "")
        if not room_id:
            continue
        room_ids.add(room_id)
        identities.upsert_membership(
            {
                **membership,
                "display_name": display_name,
            }
        )
    for room_id in room_ids:
        if not rooms.participant(room_id, participant_id):
            continue
        with rooms.transaction(room_id) as transaction:
            transaction.update_participant_fields(
                participant_id,
                display_name=display_name,
                avatar_image_url=avatar_image_url,
            )
            transaction.append_event(
                "participant_updated",
                participant_id=participant_id,
                participant_type="human",
                display_name=display_name,
                avatar_image_url=avatar_image_url,
            )
