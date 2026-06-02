from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

PARTICIPANT_TYPES = {"human", "subscription_ai", "api", "local", "remote", "unknown"}
FRIEND_STATUS = {"online", "idle", "offline", "working", "error", "unknown"}


def room_friends_payload(
    output_root: Path,
    *,
    agents: list[dict[str, object]] | None = None,
    meetings: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    saved = read_room_friends(output_root)
    saved_ids = {str(friend.get("friend_id") or "") for friend in saved}
    candidates = [
        candidate
        for candidate in room_friend_candidates(agents or [], meetings or [])
        if str(candidate.get("friend_id") or "") not in saved_ids
    ]
    return {"friends": saved, "candidates": candidates}


def read_room_friends(output_root: Path) -> list[dict[str, object]]:
    state = _read_state(output_root)
    friends = state.get("friends")
    if not isinstance(friends, dict):
        return []
    return [
        public_friend(value, fallback_id=friend_id)
        for friend_id, value in sorted(friends.items(), key=lambda item: str(item[0]))
    ]


def upsert_room_friend(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    state = _read_state(output_root)
    friends = state.setdefault("friends", {})
    if not isinstance(friends, dict):
        friends = {}
        state["friends"] = friends
    friend = public_friend(payload, fallback_id=_new_friend_id(payload))
    now = datetime.now(UTC).isoformat()
    existing = friends.get(friend["friend_id"]) if isinstance(friends.get(friend["friend_id"]), dict) else {}
    if isinstance(existing, dict) and existing.get("created_at"):
        friend["created_at"] = clean_friend_text(existing.get("created_at"), limit=64)
    else:
        friend["created_at"] = now
    friend["updated_at"] = now
    friends[friend["friend_id"]] = friend
    _write_state(output_root, state)
    return {"friend": friend, "friends": read_room_friends(output_root)}


def room_friend_candidates(
    agents: list[dict[str, object]],
    meetings: list[dict[str, object]],
) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    seen: set[str] = set()
    for agent in agents:
        agent_id = clean_friend_text(agent.get("agent_id"), limit=128)
        if not agent_id:
            continue
        candidate = public_friend(
            {
                "friend_id": f"agent-{agent_id}",
                "display_name": agent.get("display_name") or agent_id,
                "participant_type": infer_participant_type(agent.get("provider_kind"), agent.get("connection_kind")),
                "provider_kind": agent.get("provider_kind"),
                "connection_kind": agent.get("connection_kind"),
                "source_agent_id": agent_id,
                "last_meeting_id": agent.get("meeting_id"),
                "status": agent.get("status") or "unknown",
                "source": "live_agent",
            },
            fallback_id=f"agent-{agent_id}",
        )
        candidates.append(candidate)
        seen.add(str(candidate["friend_id"]))
    for meeting in meetings[:20]:
        meeting_id = clean_friend_text(meeting.get("meeting_id"), limit=128)
        if not meeting_id:
            continue
        friend_id = f"meeting-{meeting_id}"
        if friend_id in seen:
            continue
        candidates.append(
            public_friend(
                {
                    "friend_id": friend_id,
                    "display_name": meeting.get("topic") or meeting_id,
                    "participant_type": "unknown",
                    "provider_kind": "meeting",
                    "connection_kind": "room_history",
                    "last_meeting_id": meeting_id,
                    "status": "offline",
                    "source": "meeting_history",
                },
                fallback_id=friend_id,
            )
        )
        seen.add(friend_id)
    return candidates


def public_friend(value: object, *, fallback_id: str) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    friend_id = clean_friend_id(source.get("friend_id") or fallback_id)
    participant_type = clean_friend_text(source.get("participant_type"), limit=32)
    if participant_type not in PARTICIPANT_TYPES:
        participant_type = infer_participant_type(source.get("provider_kind"), source.get("connection_kind"))
    status = clean_friend_text(source.get("status"), limit=32)
    if status not in FRIEND_STATUS:
        status = "unknown"
    display_name = clean_friend_text(source.get("display_name"), limit=80) or friend_id
    return {
        "friend_id": friend_id,
        "display_name": display_name,
        "participant_type": participant_type,
        "provider_kind": clean_friend_text(source.get("provider_kind"), limit=80),
        "connection_kind": clean_friend_text(source.get("connection_kind"), limit=80),
        "source_agent_id": clean_friend_text(source.get("source_agent_id"), limit=128),
        "last_meeting_id": clean_friend_text(source.get("last_meeting_id"), limit=128),
        "status": status,
        "source": clean_friend_text(source.get("source"), limit=40),
        "created_at": clean_friend_text(source.get("created_at"), limit=64),
        "updated_at": clean_friend_text(source.get("updated_at"), limit=64),
    }


def infer_participant_type(provider_kind: object, connection_kind: object = "") -> str:
    provider = clean_friend_text(provider_kind, limit=80).lower()
    connection = clean_friend_text(connection_kind, limit=80).lower()
    if provider in {"human", "manual"}:
        return "human"
    if provider in {"anthropic", "gemini", "grok", "deepseek", "openai"}:
        return "api"
    if provider in {"local_openai_compatible", "ollama", "lmstudio", "llama", "mock"}:
        return "local"
    if provider in {"remote_http_bridge", "native_remote_room_client"} or connection == "remote_bridge":
        return "remote"
    if any(
        token in provider
        for token in (
            "codex",
            "claude",
            "kiro",
            "cursor",
            "antigravity",
            "grok_live",
            "hermes",
        )
    ):
        return "subscription_ai"
    return "unknown"


def clean_friend_id(value: object) -> str:
    text = clean_friend_text(value, limit=128)
    text = text.replace("/", "-").replace("\\", "-")
    text = re.sub(r"[^A-Za-z0-9._:@-]+", "-", text).strip(".:-_")
    return text or uuid4().hex


def clean_friend_text(value: object, *, limit: int) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _new_friend_id(payload: dict[str, object]) -> str:
    source_agent_id = clean_friend_text(payload.get("source_agent_id"), limit=128)
    if source_agent_id:
        return f"agent-{clean_friend_id(source_agent_id)}"
    return uuid4().hex


def _state_path(output_root: Path) -> Path:
    return output_root / "room_friends.json"


def _read_state(output_root: Path) -> dict[str, object]:
    path = _state_path(output_root)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"friends": {}}
    return state if isinstance(state, dict) else {"friends": {}}


def _write_state(output_root: Path, state: dict[str, object]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    _state_path(output_root).write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
