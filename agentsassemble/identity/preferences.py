"""Shared identity rules for room preference ownership."""
from __future__ import annotations

from agentsassemble.room.text import clean_room_text


def canonical_user_id(value: object) -> str:
    raw = str(value or "")
    cleaned = clean_room_text(raw, limit=128)
    if not cleaned or cleaned != raw:
        raise ValueError("user_id is required and must be canonical.")
    return cleaned
