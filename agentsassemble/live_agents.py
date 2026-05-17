from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentsassemble.meeting_events import clean_lobby_text

LIVE_AGENT_STATE = "live_agents.json"
PERSISTED_STATUSES = {"online", "working", "offline", "error"}
LIVE_AGENT_CONNECTION_KINDS = {"codex_resume", "local_cli", "live_session", "remote_bridge", "manual"}
DEFAULT_STALE_AFTER_SECONDS = 180
LIVE_AGENT_STATE_LOCK = threading.Lock()


def read_live_agents(
    output_root: Path,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
) -> list[dict[str, object]]:
    state = _read_state(output_root)
    current_time = now or datetime.now(UTC)
    return [
        _with_inferred_status(agent, now=current_time, stale_after_seconds=stale_after_seconds)
        for agent in _agent_entries(state)
    ]


def connect_live_agent(
    output_root: Path,
    payload: dict[str, object],
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    current_time = now or datetime.now(UTC)
    timestamp = current_time.isoformat()
    agent_id = clean_lobby_text(payload.get("agent_id"), limit=64)
    if not agent_id:
        raise ValueError("Agent id is required.")

    with LIVE_AGENT_STATE_LOCK:
        state = _read_state(output_root)
        agents = _agent_entries(state)
        existing = next((agent for agent in agents if agent.get("agent_id") == agent_id), {})
        agent = {
            "agent_id": agent_id,
            "display_name": clean_lobby_text(payload.get("display_name"), limit=64)
            or clean_lobby_text(existing.get("display_name"), limit=64)
            or agent_id,
            "provider_kind": clean_lobby_text(payload.get("provider_kind"), limit=64)
            or clean_lobby_text(existing.get("provider_kind"), limit=64)
            or "manual",
            "connection_kind": _normalize_connection_kind(payload.get("connection_kind") or existing.get("connection_kind")),
            "status": _normalize_persisted_status(payload.get("status") or existing.get("status") or "online"),
            "engagement_mode": clean_lobby_text(payload.get("engagement_mode"), limit=64)
            or clean_lobby_text(existing.get("engagement_mode"), limit=64)
            or "mentioned",
            "meeting_id": clean_lobby_text(payload.get("meeting_id"), limit=128)
            or clean_lobby_text(existing.get("meeting_id"), limit=128),
            "session_id": clean_lobby_text(payload.get("session_id"), limit=128)
            or clean_lobby_text(existing.get("session_id"), limit=128),
            "endpoint": clean_lobby_text(payload.get("endpoint"), limit=240)
            or clean_lobby_text(existing.get("endpoint"), limit=240),
            "capabilities": _clean_capabilities(payload.get("capabilities") or existing.get("capabilities")),
            "last_error": clean_lobby_text(payload.get("last_error"), limit=500)
            or clean_lobby_text(existing.get("last_error"), limit=500),
            "last_reply_at": clean_lobby_text(payload.get("last_reply_at"), limit=64)
            or clean_lobby_text(existing.get("last_reply_at"), limit=64),
            "last_observed_event_id": clean_lobby_text(payload.get("last_observed_event_id"), limit=128)
            or clean_lobby_text(existing.get("last_observed_event_id"), limit=128),
            "created_at": clean_lobby_text(existing.get("created_at"), limit=64) or timestamp,
            "updated_at": timestamp,
            "last_seen_at": timestamp,
        }
        _upsert_agent(agents, agent)
        _write_state(output_root, {"agents": agents})
        return agent


def heartbeat_live_agent(
    output_root: Path,
    agent_id: str,
    *,
    status: str = "online",
    metadata: dict[str, object] | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    current_time = now or datetime.now(UTC)
    timestamp = current_time.isoformat()
    clean_agent_id = clean_lobby_text(agent_id, limit=64)
    if not clean_agent_id:
        raise ValueError("Agent id is required.")

    metadata = metadata or {}
    with LIVE_AGENT_STATE_LOCK:
        state = _read_state(output_root)
        agents = _agent_entries(state)
        existing = next((agent for agent in agents if agent.get("agent_id") == clean_agent_id), {})
        if existing:
            agent = dict(existing)
            agent["status"] = _normalize_persisted_status(status)
            agent["updated_at"] = timestamp
            agent["last_seen_at"] = timestamp
        else:
            agent = {
                "agent_id": clean_agent_id,
                "display_name": clean_agent_id,
                "provider_kind": "manual",
                "connection_kind": "manual",
                "status": _normalize_persisted_status(status),
                "engagement_mode": "mentioned",
                "meeting_id": "",
                "session_id": "",
                "endpoint": "",
                "capabilities": [],
                "last_error": "",
                "last_reply_at": "",
                "last_observed_event_id": "",
                "created_at": timestamp,
                "updated_at": timestamp,
                "last_seen_at": timestamp,
            }
        for key, limit in (("last_error", 500), ("last_reply_at", 64), ("last_observed_event_id", 128)):
            if key in metadata:
                agent[key] = clean_lobby_text(metadata.get(key), limit=limit)
        _upsert_agent(agents, agent)
        _write_state(output_root, {"agents": agents})
        return agent


def _read_state(output_root: Path) -> dict[str, Any]:
    path = output_root / LIVE_AGENT_STATE
    if not path.exists():
        return {"agents": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"agents": []}
    return data if isinstance(data, dict) else {"agents": []}


def _write_state(output_root: Path, state: dict[str, object]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / LIVE_AGENT_STATE
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)


def _agent_entries(state: dict[str, Any]) -> list[dict[str, object]]:
    agents = state.get("agents")
    if not isinstance(agents, list):
        return []
    return [agent for agent in agents if isinstance(agent, dict)]


def _upsert_agent(agents: list[dict[str, object]], agent: dict[str, object]) -> None:
    for index, existing in enumerate(agents):
        if existing.get("agent_id") == agent.get("agent_id"):
            agents[index] = agent
            return
    agents.append(agent)


def _with_inferred_status(
    agent: dict[str, object],
    *,
    now: datetime,
    stale_after_seconds: int,
) -> dict[str, object]:
    visible = dict(agent)
    status = clean_lobby_text(visible.get("status"), limit=32)
    if status not in {"online", "working"}:
        return visible
    last_seen = _parse_timestamp(visible.get("last_seen_at"))
    if last_seen is None:
        return visible
    if (now - last_seen).total_seconds() > stale_after_seconds:
        visible["status"] = "stale"
    return visible


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalize_connection_kind(value: object) -> str:
    cleaned = clean_lobby_text(value, limit=64)
    return cleaned if cleaned in LIVE_AGENT_CONNECTION_KINDS else "manual"


def _normalize_persisted_status(value: object) -> str:
    cleaned = clean_lobby_text(value, limit=32)
    return cleaned if cleaned in PERSISTED_STATUSES else "online"


def _clean_capabilities(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    capabilities = []
    seen = set()
    for item in value:
        cleaned = clean_lobby_text(item, limit=64)
        if not cleaned or cleaned in seen:
            continue
        capabilities.append(cleaned)
        seen.add(cleaned)
    return capabilities
