"""Bounded text normalization shared by current room code."""
from __future__ import annotations


def clean_room_text(value: object, limit: int) -> str:
    normalized = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    return normalized[:limit].strip()


__all__ = ["clean_room_text"]
