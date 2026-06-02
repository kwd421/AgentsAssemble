from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.room_friends import read_room_friends

ROOM_FRIEND_DMS_DIR = "room_friend_dms"
ROOM_FRIEND_DM_MESSAGE_LIMIT = 2000
ROOM_FRIEND_DM_DEFAULT_LIMIT = 80


def room_friend_dm_payload(
    output_root: Path,
    friend_id: str,
    *,
    limit: int = ROOM_FRIEND_DM_DEFAULT_LIMIT,
) -> dict[str, object]:
    friend = _require_saved_friend(output_root, friend_id)
    return {
        "friend": friend,
        "events": read_room_friend_dm(output_root, friend_id, limit=limit),
    }


def read_room_friend_dm(
    output_root: Path,
    friend_id: str,
    *,
    limit: int = ROOM_FRIEND_DM_DEFAULT_LIMIT,
) -> list[dict[str, object]]:
    _require_saved_friend(output_root, friend_id)
    path = _friend_dm_path(output_root, friend_id)
    if not path.exists():
        return []
    events: list[dict[str, object]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-max(1, limit) :]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            event = _normalize_dm_event(payload, fallback_friend_id=friend_id, preserve_id=True)
            if event.get("friend_id") == friend_id:
                events.append(event)
    return events


def append_room_friend_dm_event(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    friend_id = clean_lobby_text(payload.get("friend_id"), limit=96)
    _require_saved_friend(output_root, friend_id)
    output_root.mkdir(parents=True, exist_ok=True)
    path = _friend_dm_path(output_root, friend_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = _normalize_dm_event(payload, fallback_friend_id=friend_id)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event


def _require_saved_friend(output_root: Path, friend_id: str) -> dict[str, object]:
    clean_friend_id = clean_lobby_text(friend_id, limit=96)
    if not clean_friend_id:
        raise ValueError("friend_id is required")
    for friend in read_room_friends(output_root):
        if clean_lobby_text(friend.get("friend_id"), limit=96) == clean_friend_id:
            return friend
    raise ValueError("Saved room friend was not found")


def _normalize_dm_event(
    payload: dict[str, Any],
    *,
    fallback_friend_id: str,
    preserve_id: bool = False,
) -> dict[str, object]:
    side = clean_lobby_text(payload.get("side") or "mine", limit=32)
    if side not in {"mine", "other"}:
        side = "mine"
    event_id = clean_lobby_text(payload.get("id"), limit=64) if preserve_id else ""
    return {
        "id": event_id or uuid4().hex[:12],
        "friend_id": clean_lobby_text(payload.get("friend_id") or fallback_friend_id, limit=96),
        "created_at": clean_lobby_text(payload.get("created_at"), limit=64) or datetime.now(UTC).isoformat(),
        "name": clean_lobby_text(payload.get("name") or "나", limit=32) or "나",
        "side": side,
        "message": clean_lobby_text(payload.get("message"), limit=ROOM_FRIEND_DM_MESSAGE_LIMIT),
    }


def _friend_dm_path(output_root: Path, friend_id: str) -> Path:
    digest = hashlib.sha256(clean_lobby_text(friend_id, limit=96).encode("utf-8")).hexdigest()[:24]
    return output_root / ROOM_FRIEND_DMS_DIR / f"{digest}.jsonl"
