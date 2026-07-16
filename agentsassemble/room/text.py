"""Bounded text normalization shared by current room code."""
from __future__ import annotations

import unicodedata


def clean_room_text(value: object, limit: int) -> str:
    normalized = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    return normalized[:limit].strip()


def has_room_visible_text(value: object) -> bool:
    """Return false for whitespace/control-only output such as zero-width silence."""
    return any(
        not character.isspace() and unicodedata.category(character) not in {"Cc", "Cf"}
        for character in str(value or "")
    )


__all__ = ["clean_room_text", "has_room_visible_text"]
