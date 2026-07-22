"""Primitive value normalization shared by persona card modules."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def _safe_persona_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    return cleaned[:80] or "persona"


def _joined_strings(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if isinstance(item, str))
    return ""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _lore_extra(data: dict[str, Any]) -> dict[str, Any]:
    known = {
        "key",
        "keys",
        "content",
        "secondkey",
        "secondary_keys",
        "comment",
        "name",
        "always_active",
        "alwaysActive",
        "constant",
        "selective",
        "use_regex",
        "useRegex",
        "insert_order",
        "insertorder",
        "insertion_order",
        "position",
        "role",
        "enabled",
        "case_sensitive",
        "priority",
        "extra",
    }
    return {key: value for key, value in data.items() if key not in known}


def _json_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text(value: object) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def _raw_text(value: object) -> str:
    return str(value) if isinstance(value, str) else ""


def _int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _preview(value: object, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _prompt_text(value: object, *, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]
