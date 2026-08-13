from __future__ import annotations

from agentsassemble.room.text import clean_room_text


def assistant_text(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    info = value.get("info") if isinstance(value.get("info"), dict) else {}
    if str(info.get("role") or "") != "assistant":
        return ""
    parts = value.get("parts") if isinstance(value.get("parts"), list) else []
    return "".join(
        str(part.get("text") or "")
        for part in parts
        if isinstance(part, dict) and part.get("type") == "text"
    ).strip()


def assistant_parent_id(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    info = value.get("info") if isinstance(value.get("info"), dict) else {}
    if str(info.get("role") or "") != "assistant":
        return ""
    return clean_room_text(info.get("parentID"), limit=128)


__all__ = ["assistant_parent_id", "assistant_text"]
