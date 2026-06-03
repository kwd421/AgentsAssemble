from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

PROFILE_TEXT_LIMIT = 120
CUSTOM_STATUS_LIMIT = 160
AVATAR_LABEL_LIMIT = 2
IMAGE_URL_LIMIT = 240
VALID_STATUSES = {"online", "idle", "dnd", "offline"}
VALID_BANNER_PRESETS = {"default", "forest", "midnight", "ember", "custom"}
DEFAULT_PROFILE = {
    "display_name": "SeiNel",
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


def read_user_profile(output_root: Path) -> dict[str, object]:
    return {"profile": public_user_profile(_read_state(output_root).get("profile"))}


def update_user_profile(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    state = _read_state(output_root)
    current = state.get("profile") if isinstance(state.get("profile"), dict) else {}
    profile = {
        **public_user_profile(current),
        **_clean_profile_update(payload),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if not profile.get("created_at"):
        profile["created_at"] = profile["updated_at"]
    state["profile"] = public_user_profile(profile)
    _write_state(output_root, state)
    return {"profile": state["profile"]}


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


def _state_path(output_root: Path) -> Path:
    return output_root / "user_profile.json"


def _read_state(output_root: Path) -> dict[str, object]:
    path = _state_path(output_root)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"profile": DEFAULT_PROFILE}
    return state if isinstance(state, dict) else {"profile": DEFAULT_PROFILE}


def _write_state(output_root: Path, state: dict[str, object]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    _state_path(output_root).write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
