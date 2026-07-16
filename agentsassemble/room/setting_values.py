"""Shared value rules for room-global settings and legacy migration."""
from __future__ import annotations

import re


ROOM_TEXT_LIMIT = 160
IMAGE_URL_LIMIT = 240

VALID_BANNER_PRESETS = frozenset({"default", "forest", "midnight", "ember", "custom"})
VALID_INVITE_SCOPES = frozenset({"room", "read_only"})
CONVERSATION_MODES = frozenset({"ordered", "continuous", "ambient"})


def clean_room_text(value: object, *, limit: int) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def clean_short_label(value: object) -> str:
    return clean_room_text(value, limit=2).upper()[:2]


def clean_room_asset_url(value: object) -> str:
    text = clean_room_text(value, limit=IMAGE_URL_LIMIT)
    if not text:
        return ""
    if text.startswith("/api/attachments/") and re.fullmatch(
        r"/api/attachments/[A-Za-z0-9_-]{8,64}\?(view|download)=1",
        text,
    ):
        return text
    return ""
