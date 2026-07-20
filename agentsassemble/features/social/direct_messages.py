"""Optional friend direct-message records and live-agent delivery bridge."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from agentsassemble.legacy.meeting.core.events import clean_lobby_text
from agentsassemble.features.social.friends import read_room_friends

ROOM_FRIEND_DMS_DIR = "room_friend_dms"
ROOM_FRIEND_DM_MESSAGE_LIMIT = 2000
ROOM_FRIEND_DM_DEFAULT_LIMIT = 80
DIRECT_DM_AGENT_MISSING_MESSAGE = "실제 AI 세션을 찾을 수 없습니다."
DIRECT_DM_SESSION_MISSING_MESSAGE = "세션이 존재하지 않습니다. 먼저 세션을 시작하거나 이 AI를 다시 친구로 저장하세요."
DIRECT_DM_ACTIVE_AGENT_STATUSES = {"online", "working", "ready", "running"}
DIRECT_DM_AI_TYPES = {"subscription_ai", "api", "local", "remote"}


def room_friend_dm_payload(
    output_root: Path,
    friend_id: str,
    *,
    limit: int = ROOM_FRIEND_DM_DEFAULT_LIMIT,
) -> dict[str, object]:
    friend = _require_saved_friend(output_root, friend_id)
    events = read_room_friend_dm(output_root, friend_id, limit=limit)
    return {
        "friend": friend,
        "events": events,
        "delivery": _latest_delivery(events),
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


def enqueue_room_friend_direct_dm(
    output_root: Path,
    payload: dict[str, object],
    *,
    live_agents: list[dict[str, object]],
    process_groups: list[dict[str, object]] | None = None,
    resume_callback: Callable[[dict[str, object]], object] | None = None,
) -> dict[str, object]:
    friend_id = clean_lobby_text(payload.get("friend_id"), limit=96)
    friend = _require_saved_friend(output_root, friend_id)
    message = clean_lobby_text(payload.get("message"), limit=ROOM_FRIEND_DM_MESSAGE_LIMIT)
    if not message:
        raise ValueError("Message is required.")
    target_agent_id = _direct_dm_target_agent_id(friend)
    if not target_agent_id:
        raise ValueError(DIRECT_DM_AGENT_MISSING_MESSAGE)
    live_agent = _live_agent_by_id(live_agents, target_agent_id)
    resume_result: object = None
    if not _live_agent_is_active(live_agent):
        if payload.get("resume_if_needed") is False:
            raise ValueError(DIRECT_DM_SESSION_MISSING_MESSAGE)
        group = _process_group_for_agent(process_groups or [], target_agent_id)
        if group is None or not str(group.get("config_path") or "").strip():
            raise ValueError(DIRECT_DM_SESSION_MISSING_MESSAGE)
        if resume_callback is not None:
            resume_result = resume_callback(group)
    event = append_room_friend_dm_event(
        output_root,
        {
            "friend_id": friend_id,
            "name": clean_lobby_text(payload.get("name") or "나", limit=32) or "나",
            "side": "mine",
            "message": message,
            "target_agent_id": target_agent_id,
            "delivery_status": "queued",
        },
    )
    response = {
        "event": event,
        **room_friend_dm_payload(output_root, friend_id),
    }
    if resume_result is not None:
        response["resume"] = resume_result
    return response


def read_live_agent_dm_events(
    output_root: Path,
    agent_id: str,
    *,
    after_event_id: str = "",
    limit: int = ROOM_FRIEND_DM_DEFAULT_LIMIT,
) -> list[dict[str, object]]:
    target_agent_id = clean_lobby_text(agent_id, limit=128)
    if not target_agent_id:
        return []
    events: list[dict[str, object]] = []
    for friend in read_room_friends(output_root):
        if _direct_dm_target_agent_id(friend) != target_agent_id:
            continue
        for event in read_room_friend_dm(output_root, str(friend.get("friend_id") or ""), limit=max(limit, ROOM_FRIEND_DM_DEFAULT_LIMIT)):
            if event.get("side") != "mine":
                continue
            if clean_lobby_text(event.get("target_agent_id"), limit=128) != target_agent_id:
                continue
            if event.get("delivery_status") == "failed":
                continue
            events.append(event)
    return _events_after_id(events[-max(1, limit) :], after_event_id)


def append_live_agent_dm_reply(
    output_root: Path,
    agent_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    target_agent_id = clean_lobby_text(agent_id, limit=128)
    source_event_id = clean_lobby_text(payload.get("source_event_id"), limit=128)
    message = clean_lobby_text(payload.get("message") or payload.get("content"), limit=ROOM_FRIEND_DM_MESSAGE_LIMIT)
    if not target_agent_id:
        raise ValueError("Agent id is required.")
    if not source_event_id:
        raise ValueError("source_event_id is required.")
    if not message:
        raise ValueError("Message is required.")
    source = _dm_source_event_for_agent(output_root, target_agent_id, source_event_id)
    if source is None:
        raise ValueError("Direct DM source event was not found.")
    friend = _require_saved_friend(output_root, str(source.get("friend_id") or ""))
    event = append_room_friend_dm_event(
        output_root,
        {
            "friend_id": source["friend_id"],
            "name": clean_lobby_text(friend.get("display_name"), limit=32)
            or clean_lobby_text(payload.get("name"), limit=32)
            or target_agent_id,
            "side": "other",
            "message": message,
            "target_agent_id": target_agent_id,
            "delivery_status": "delivered",
            "source_event_id": source_event_id,
            "reply_to_event_id": source_event_id,
        },
    )
    return {
        "event": event,
        **room_friend_dm_payload(output_root, str(source["friend_id"])),
    }


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
    delivery_status = clean_lobby_text(payload.get("delivery_status"), limit=32)
    if delivery_status not in {"queued", "delivered", "failed"}:
        delivery_status = "delivered" if side == "other" else "queued"
    return {
        "id": event_id or uuid4().hex[:12],
        "friend_id": clean_lobby_text(payload.get("friend_id") or fallback_friend_id, limit=96),
        "created_at": clean_lobby_text(payload.get("created_at"), limit=64) or datetime.now(UTC).isoformat(),
        "name": clean_lobby_text(payload.get("name") or "나", limit=32) or "나",
        "side": side,
        "message": clean_lobby_text(payload.get("message"), limit=ROOM_FRIEND_DM_MESSAGE_LIMIT),
        "target_agent_id": clean_lobby_text(payload.get("target_agent_id"), limit=128),
        "delivery_status": delivery_status,
        "error": clean_lobby_text(payload.get("error"), limit=240),
        "source_event_id": clean_lobby_text(payload.get("source_event_id"), limit=128),
        "reply_to_event_id": clean_lobby_text(payload.get("reply_to_event_id"), limit=128),
    }


def _direct_dm_target_agent_id(friend: dict[str, object]) -> str:
    participant_type = clean_lobby_text(friend.get("participant_type"), limit=32)
    if participant_type not in DIRECT_DM_AI_TYPES:
        return ""
    return clean_lobby_text(friend.get("source_agent_id") or friend.get("agent_id"), limit=128)


def _live_agent_by_id(live_agents: list[dict[str, object]], agent_id: str) -> dict[str, object] | None:
    for agent in live_agents:
        if clean_lobby_text(agent.get("agent_id"), limit=128) == agent_id:
            return agent
    return None


def _live_agent_is_active(agent: dict[str, object] | None) -> bool:
    if not agent:
        return False
    return clean_lobby_text(agent.get("status"), limit=32) in DIRECT_DM_ACTIVE_AGENT_STATUSES


def _process_group_for_agent(process_groups: list[dict[str, object]], agent_id: str) -> dict[str, object] | None:
    for group in process_groups:
        agents = group.get("agents")
        if isinstance(agents, list):
            for agent in agents:
                if isinstance(agent, dict) and clean_lobby_text(agent.get("agent_id"), limit=128) == agent_id:
                    return group
                if clean_lobby_text(agent, limit=128) == agent_id:
                    return group
        if clean_lobby_text(group.get("agent_id"), limit=128) == agent_id:
            return group
    return None


def _dm_source_event_for_agent(output_root: Path, agent_id: str, source_event_id: str) -> dict[str, object] | None:
    for event in read_live_agent_dm_events(output_root, agent_id, after_event_id="", limit=1000):
        if clean_lobby_text(event.get("id"), limit=128) == source_event_id:
            return event
    return None


def _events_after_id(events: list[dict[str, object]], after_event_id: str) -> list[dict[str, object]]:
    cursor = clean_lobby_text(after_event_id, limit=128)
    if not cursor:
        return list(events)
    for index, event in enumerate(events):
        if clean_lobby_text(event.get("id"), limit=128) == cursor:
            return events[index + 1 :]
    return list(events)


def _latest_delivery(events: list[dict[str, object]]) -> dict[str, object]:
    for index in range(len(events) - 1, -1, -1):
        event = events[index]
        if event.get("side") == "mine":
            event_id = event.get("id") or ""
            if event_id and any(
                later.get("side") == "other"
                and (later.get("reply_to_event_id") == event_id or later.get("source_event_id") == event_id)
                for later in events[index + 1 :]
            ):
                return {
                    "status": "delivered",
                    "error": "",
                    "source_event_id": event_id,
                    "target_agent_id": event.get("target_agent_id") or "",
                }
            return {
                "status": event.get("delivery_status") or "queued",
                "error": event.get("error") or "",
                "source_event_id": event_id,
                "target_agent_id": event.get("target_agent_id") or "",
            }
    return {"status": "idle", "error": "", "source_event_id": "", "target_agent_id": ""}


def _friend_dm_path(output_root: Path, friend_id: str) -> Path:
    digest = hashlib.sha256(clean_lobby_text(friend_id, limit=96).encode("utf-8")).hexdigest()[:24]
    return output_root / ROOM_FRIEND_DMS_DIR / f"{digest}.jsonl"
