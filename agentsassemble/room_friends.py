from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentsassemble.meeting_events import clean_lobby_text

ROOM_FRIENDS_FILE = "room_friends.json"
ROOM_FRIEND_TYPES = {"human", "subscription_ai", "api", "local", "remote", "unknown"}


def normalize_room_friend_type(value: object) -> str:
    normalized = clean_lobby_text(value, limit=32).lower().replace("-", "_")
    aliases = {
        "person": "human",
        "user": "human",
        "people": "human",
        "subscriber_ai": "subscription_ai",
        "subscription": "subscription_ai",
        "provider": "subscription_ai",
        "model_api": "api",
        "local_model": "local",
        "lmstudio": "local",
        "llama": "local",
        "native_remote_room_client": "remote",
        "remote_http_bridge": "remote",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in ROOM_FRIEND_TYPES else "unknown"


def room_friend_type_for_agent(agent: dict[str, object]) -> str:
    text = " ".join(
        clean_lobby_text(agent.get(key), limit=128).lower()
        for key in ("provider_kind", "connection_kind", "join_semantics", "agent_id", "display_name")
    )
    if any(token in text for token in ("remote_http_bridge", "native_remote_room_client", "remote_bridge")):
        return "remote"
    if any(token in text for token in ("manual", "guest", "human")):
        return "human"
    if any(token in text for token in ("lmstudio", "llama", "ollama", "local_model")):
        return "local"
    if any(token in text for token in ("api", "deepseek", "openai", "anthropic")):
        return "api"
    if any(
        token in text
        for token in (
            "claude",
            "codex",
            "kiro",
            "cursor",
            "antigravity",
            "gemini",
            "grok",
            "hermes",
        )
    ):
        return "subscription_ai"
    return "unknown"


def read_room_friends(output_root: Path) -> list[dict[str, object]]:
    path = output_root / ROOM_FRIENDS_FILE
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    friends = payload.get("friends")
    if not isinstance(friends, list):
        return []
    normalized = [_normalize_friend_record(friend) for friend in friends if isinstance(friend, dict)]
    return [friend for friend in normalized if friend.get("friend_id")]


def upsert_room_friend(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    output_root.mkdir(parents=True, exist_ok=True)
    friends = read_room_friends(output_root)
    now = datetime.now(UTC).isoformat()
    friend = _normalize_friend_record(payload)
    friend_id = clean_lobby_text(friend.get("friend_id"), limit=96) or _friend_id_from_payload(friend)
    friend["friend_id"] = friend_id
    if not friend.get("created_at"):
        friend["created_at"] = now
    friend["updated_at"] = now
    by_id = {str(existing.get("friend_id")): existing for existing in friends}
    if friend_id in by_id:
        existing = dict(by_id[friend_id])
        existing.update({key: value for key, value in friend.items() if value not in ("", [], {})})
        existing["updated_at"] = now
        friend = existing
    by_id[friend_id] = friend
    ordered = sorted(by_id.values(), key=lambda item: str(item.get("updated_at", "")), reverse=True)
    _write_room_friends(output_root, ordered)
    return friend


def room_friend_suggestions_from_agents(
    agents: list[dict[str, object]],
    friends: list[dict[str, object]],
) -> list[dict[str, object]]:
    saved_agent_ids = {
        clean_lobby_text(friend.get("agent_id"), limit=128)
        for friend in friends
        if clean_lobby_text(friend.get("agent_id"), limit=128)
    }
    suggestions: list[dict[str, object]] = []
    for agent in agents:
        agent_id = clean_lobby_text(agent.get("agent_id"), limit=128)
        if not agent_id or agent_id in saved_agent_ids:
            continue
        suggestions.append(
            _normalize_friend_record(
                {
                    "friend_id": f"agent:{agent_id}",
                    "display_name": agent.get("display_name") or agent_id,
                    "handle": agent_id,
                    "participant_type": room_friend_type_for_agent(agent),
                    "provider_kind": agent.get("provider_kind"),
                    "connection_kind": agent.get("connection_kind"),
                    "agent_id": agent_id,
                    "source_agent_id": agent_id,
                    "last_meeting_id": agent.get("meeting_id"),
                    "status": agent.get("status") or "unknown",
                    "source": "live_agent",
                    "last_seen_at": agent.get("last_seen_at"),
                }
            )
        )
    return suggestions


def room_friends_payload(output_root: Path, agents: list[dict[str, object]]) -> dict[str, object]:
    friends = read_room_friends(output_root)
    suggestions = room_friend_suggestions_from_agents(agents, friends)
    return {
        "friends": friends,
        "suggestions": suggestions,
        "candidates": suggestions,
        "types": sorted(ROOM_FRIEND_TYPES),
    }


def _normalize_friend_record(payload: dict[str, Any]) -> dict[str, object]:
    display_name = clean_lobby_text(payload.get("display_name") or payload.get("name"), limit=64)
    agent_id = clean_lobby_text(payload.get("agent_id") or payload.get("source_agent_id"), limit=128)
    handle = clean_lobby_text(payload.get("handle") or agent_id, limit=128)
    participant_type = normalize_room_friend_type(payload.get("participant_type") or payload.get("type"))
    return {
        "friend_id": clean_lobby_text(payload.get("friend_id"), limit=96),
        "display_name": display_name or handle or "Friend",
        "handle": handle,
        "participant_type": participant_type,
        "provider_kind": clean_lobby_text(payload.get("provider_kind"), limit=64),
        "connection_kind": clean_lobby_text(payload.get("connection_kind"), limit=64),
        "agent_id": agent_id,
        "source_agent_id": clean_lobby_text(payload.get("source_agent_id") or agent_id, limit=128),
        "last_meeting_id": clean_lobby_text(payload.get("last_meeting_id") or payload.get("meeting_id"), limit=128),
        "status": clean_lobby_text(payload.get("status") or "unknown", limit=32),
        "source": clean_lobby_text(payload.get("source") or "manual", limit=32),
        "created_at": clean_lobby_text(payload.get("created_at"), limit=64),
        "updated_at": clean_lobby_text(payload.get("updated_at"), limit=64),
        "last_seen_at": clean_lobby_text(payload.get("last_seen_at"), limit=64),
    }


def _friend_id_from_payload(payload: dict[str, object]) -> str:
    basis = clean_lobby_text(
        payload.get("source_agent_id") or payload.get("agent_id") or payload.get("handle") or payload.get("display_name"),
        limit=96,
    )
    slug = re.sub(r"[^A-Za-z0-9_.:-]+", "-", basis).strip("-").lower()
    return f"friend:{slug or 'manual'}"


def _write_room_friends(output_root: Path, friends: list[dict[str, object]]) -> None:
    path = output_root / ROOM_FRIENDS_FILE
    path.write_text(
        json.dumps({"friends": friends}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
