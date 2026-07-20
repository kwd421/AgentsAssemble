from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from agentsassemble.legacy.meeting.core.events import clean_lobby_text
from agentsassemble.persona_cards import load_persona_card


CHARACTER_MODES = {"off", "on", "work_speech_only"}


def clean_persona_card_id(value: object) -> str:
    text = clean_lobby_text(value, limit=80)
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip(".-")
    return cleaned[:80]


def normalize_character_mode(value: object, *, has_card: bool = False) -> str:
    mode = clean_lobby_text(value, limit=64)
    if mode in CHARACTER_MODES:
        return mode
    return "on" if has_card else "off"


def clean_first_message_index(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(-1, parsed)


def clean_persona_variables(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, object] = {}
    for key, item in value.items():
        clean_key = clean_persona_card_id(key)
        if not clean_key:
            continue
        if isinstance(item, str):
            clean_value = clean_lobby_text(item, limit=500)
            if clean_value:
                cleaned[clean_key] = clean_value
        elif isinstance(item, bool) or isinstance(item, int) or isinstance(item, float):
            cleaned[clean_key] = item
    return cleaned


def character_mode_snapshot(output_root: Path, bindings: list[Any]) -> dict[str, object]:
    agents = []
    for binding in bindings:
        agent_id = clean_lobby_text(getattr(binding, "agent_id", ""), limit=64)
        if not agent_id:
            continue
        card_id = clean_persona_card_id(getattr(binding, "persona_card_id", ""))
        card_path = _safe_card_path(getattr(binding, "persona_card_path", ""))
        mode = normalize_character_mode(getattr(binding, "character_mode", ""), has_card=bool(card_id or card_path))
        card = _persona_card_snapshot(output_root, card_id, card_path=card_path)
        resolved_card_id = clean_persona_card_id(card.get("card_id") or card_id)
        agents.append(
            {
                "agent_id": agent_id,
                "card_id": resolved_card_id,
                "card_hash": card["card_hash"],
                "mode": mode,
                "first_message_index": clean_first_message_index(getattr(binding, "first_message_index", 0)),
                "persona_variables": clean_persona_variables(getattr(binding, "persona_variables", {})),
                "ignored_features": card["ignored_features"],
                "source_path": card["source_path"],
            }
        )
    return {"version": 1, "agents": agents}


def _persona_card_snapshot(output_root: Path, card_id: str, *, card_path: Path | None = None) -> dict[str, object]:
    if card_path is None:
        if not card_id:
            return {"card_id": "", "card_hash": "", "ignored_features": {}, "source_path": ""}
        card_path = output_root / "personas" / card_id / "card.json"
    try:
        raw = card_path.read_bytes()
        card = load_persona_card(card_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {
            "card_id": card_id,
            "card_hash": "",
            "ignored_features": {},
            "source_path": _safe_source_path(output_root, card_path),
        }
    return {
        "card_id": clean_persona_card_id(card.id) or card_id,
        "card_hash": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "ignored_features": dict(card.ignored_features),
        "source_path": _safe_source_path(output_root, card_path),
    }


def _safe_card_path(value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return Path(value).expanduser()
    except (OSError, ValueError):
        return None


def _safe_source_path(output_root: Path, path: Path) -> str:
    try:
        return str(path.resolve(strict=False).relative_to(output_root.resolve(strict=False)))
    except ValueError:
        return ""
