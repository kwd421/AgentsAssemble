from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from math import ceil, isfinite
from pathlib import Path
from typing import Any

from agentsassemble.live_agent_context import live_agent_context_contract
from agentsassemble.character_mode import clean_persona_card_id, normalize_character_mode
from agentsassemble.live_agent_quota import LIVE_AGENT_QUOTA_FIELDS, clean_live_agent_quota_fields
from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.models import ENGAGEMENT_MODES, normalize_engagement_mode
from agentsassemble.remote_bridge_config import remote_bridge_endpoint_error

LIVE_AGENT_STATE = "live_agents.json"
PERSISTED_STATUSES = {"online", "working", "offline", "error"}
LIVE_AGENT_CONNECTION_KINDS = {
    "codex_resume",
    "local_cli",
    "live_session",
    "terminal_session",
    "remote_bridge",
    "native_remote_room_client",
    "self_service",
    "manual",
}
DEFAULT_STALE_AFTER_SECONDS = 180
OUTPUT_ONLY_FRESHNESS_FIELDS = {"heartbeat_age_seconds", "stale_after_seconds"}
LIVE_AGENT_STATE_LOCK = threading.Lock()
PRESENCE_ERROR_REDACTED = "Live-agent presence error details redacted."
PRESENCE_ATTENTION_REDACTED = "presence_attention_redacted"
SAFE_PRESENCE_ATTENTION_CODES = frozenset({"persona_context_blocked_official_turn"})
SENSITIVE_PRESENCE_ERROR_MARKERS = (
    re.compile(r"\bbearer\s+[A-Za-z0-9._-]+", re.IGNORECASE),
    re.compile(r"\b(?:auth|token|secret|password|endpoint|prompt|config|url)\b\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\b(?:env|literal):[^\s,;]+", re.IGNORECASE),
    re.compile(r"\benv\s+var\s+[A-Z][A-Z0-9_]{2,}\b"),
    re.compile(r"\benv\s+[A-Z][A-Z0-9_]{2,}\b"),
    re.compile(r"(?<!\w)\$[A-Z][A-Z0-9_]{2,}\b"),
    re.compile(r"\bprompt\s+(?:file|path|at)\s+[^\s,;)'\"]+", re.IGNORECASE),
    re.compile(r"\bconfig\s+(?:file|path|at|from|in)\s+[^\s,;)'\"]+", re.IGNORECASE),
)
SENSITIVE_PRESENCE_ERROR_PATTERNS = (
    re.compile(r"https?://\S+", re.IGNORECASE),
    re.compile(r"\b(?:localhost|(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}|\d{1,3}(?:\.\d{1,3}){3})(?::\d{2,5})?/[^\s]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{10,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9_]{10,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{10,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"\b[A-Za-z]:\\[^\s,;)'\"]+"),
    re.compile(r"(?<!\w)[^\s,;)'\"]*\.(?:json|ya?ml|toml|env|txt)\b", re.IGNORECASE),
    re.compile(r"(?<!\w)['\"]?/[^\s,;)'\"]+"),
)


def read_live_agents(
    output_root: Path,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
) -> list[dict[str, object]]:
    state = _read_state(output_root)
    current_time = now or datetime.now(UTC)
    return [
        _with_frontend_session_registration(
            output_root,
            _with_inferred_status(agent, now=current_time, stale_after_seconds=stale_after_seconds),
        )
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
        connection_kind = _normalize_connection_kind(payload.get("connection_kind") or existing.get("connection_kind"))
        endpoint = _connection_endpoint(payload, existing, connection_kind)
        if connection_kind == "remote_bridge":
            endpoint_error = remote_bridge_endpoint_error(endpoint)
            if endpoint_error:
                raise ValueError(endpoint_error)
        provider_kind = (
            clean_lobby_text(payload.get("provider_kind"), limit=64)
            or clean_lobby_text(existing.get("provider_kind"), limit=64)
            or "manual"
        )
        context_contract = live_agent_context_contract(provider_kind, connection_kind)
        requested_engagement_mode = normalize_engagement_mode(payload.get("engagement_mode"), default="mentioned")
        operator_engagement_mode = _operator_engagement_mode(existing)
        effective_engagement_mode = operator_engagement_mode or requested_engagement_mode
        persona_card_id = clean_persona_card_id(payload.get("persona_card_id") or existing.get("persona_card_id"))
        agent = {
            "agent_id": agent_id,
            "display_name": clean_lobby_text(payload.get("display_name"), limit=64)
            or clean_lobby_text(existing.get("display_name"), limit=64)
            or agent_id,
            "provider_kind": provider_kind,
            "connection_kind": connection_kind,
            "join_semantics": context_contract["join_semantics"],
            "context_durability": context_contract["context_durability"],
            "sandbox_enforcement": context_contract["sandbox_enforcement"],
            "status": _normalize_persisted_status(payload.get("status") or existing.get("status") or "online"),
            "engagement_mode": effective_engagement_mode,
            "persona_card_id": persona_card_id,
            "character_mode": normalize_character_mode(
                payload.get("character_mode") or existing.get("character_mode"),
                has_card=bool(persona_card_id),
            ),
            "meeting_id": clean_lobby_text(payload.get("meeting_id"), limit=128)
            or clean_lobby_text(existing.get("meeting_id"), limit=128),
            "session_id": clean_lobby_text(payload.get("session_id"), limit=128)
            or clean_lobby_text(existing.get("session_id"), limit=128),
            "process_group_id": clean_lobby_text(payload.get("process_group_id"), limit=128)
            or clean_lobby_text(existing.get("process_group_id"), limit=128),
            "live_agent_config_path": clean_lobby_text(payload.get("live_agent_config_path"), limit=2048)
            or clean_lobby_text(existing.get("live_agent_config_path"), limit=2048),
            "endpoint": endpoint,
            "capabilities": _clean_capabilities(payload.get("capabilities") or existing.get("capabilities")),
            "last_error": _clean_presence_last_error(payload.get("last_error"))
            or _clean_presence_last_error(existing.get("last_error")),
            "last_attention": _clean_presence_attention(payload.get("last_attention"))
            or _clean_presence_attention(existing.get("last_attention")),
            "last_reply_at": clean_lobby_text(payload.get("last_reply_at"), limit=64)
            or clean_lobby_text(existing.get("last_reply_at"), limit=64),
            "last_observed_event_id": clean_lobby_text(payload.get("last_observed_event_id"), limit=128)
            or clean_lobby_text(existing.get("last_observed_event_id"), limit=128),
            "last_observed_live_event_id": clean_lobby_text(payload.get("last_observed_live_event_id"), limit=128)
            or clean_lobby_text(existing.get("last_observed_live_event_id"), limit=128),
            "last_observed_dm_event_id": clean_lobby_text(payload.get("last_observed_dm_event_id"), limit=128)
            or clean_lobby_text(existing.get("last_observed_dm_event_id"), limit=128),
            **_live_agent_poll_interval_fields(payload, existing),
            "diagnostic": _bool_value(payload.get("diagnostic") if "diagnostic" in payload else existing.get("diagnostic")),
            "created_at": clean_lobby_text(existing.get("created_at"), limit=64) or timestamp,
            "updated_at": timestamp,
            "last_seen_at": timestamp,
        }
        agent.update(clean_live_agent_quota_fields(payload, existing))
        if operator_engagement_mode:
            agent["engagement_mode_updated_at"] = clean_lobby_text(existing.get("engagement_mode_updated_at"), limit=64)
        _upsert_agent(agents, agent)
        _write_state(output_root, {"agents": agents})
        return agent


def update_live_agent_engagement(
    output_root: Path,
    agent_id: str,
    engagement_mode: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    current_time = now or datetime.now(UTC)
    timestamp = current_time.isoformat()
    clean_agent_id = clean_lobby_text(agent_id, limit=64)
    if not clean_agent_id:
        raise ValueError("Agent id is required.")
    clean_mode = clean_lobby_text(engagement_mode, limit=64)
    if clean_mode not in ENGAGEMENT_MODES:
        expected = ", ".join(sorted(ENGAGEMENT_MODES))
        raise ValueError(f"Unknown engagement mode: {clean_mode or '(blank)'}. Expected one of: {expected}.")

    with LIVE_AGENT_STATE_LOCK:
        state = _read_state(output_root)
        agents = _agent_entries(state)
        for index, existing in enumerate(agents):
            if existing.get("agent_id") != clean_agent_id:
                continue
            agent = _without_output_only_freshness(existing)
            agent["engagement_mode"] = clean_mode
            agent["engagement_mode_updated_at"] = timestamp
            agent["updated_at"] = timestamp
            agents[index] = agent
            _write_state(output_root, {"agents": agents})
            return agent
    raise ValueError(f"Live agent {clean_agent_id} was not found.")


def update_live_agent_poll_interval(
    output_root: Path,
    agent_id: str,
    poll_interval: object,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    current_time = now or datetime.now(UTC)
    timestamp = current_time.isoformat()
    clean_agent_id = clean_lobby_text(agent_id, limit=64)
    if not clean_agent_id:
        raise ValueError("Agent id is required.")
    parsed_poll_interval = _clean_live_agent_poll_interval(poll_interval)

    with LIVE_AGENT_STATE_LOCK:
        state = _read_state(output_root)
        agents = _agent_entries(state)
        for index, existing in enumerate(agents):
            if existing.get("agent_id") != clean_agent_id:
                continue
            agent = _without_output_only_freshness(existing)
            agent["poll_interval"] = parsed_poll_interval
            agent["poll_interval_updated_at"] = timestamp
            agent["updated_at"] = timestamp
            agents[index] = agent
            _write_state(output_root, {"agents": agents})
            return agent
    raise ValueError(f"Live agent {clean_agent_id} was not found.")


def detach_live_agent_from_meeting(
    output_root: Path,
    agent_id: str,
    meeting_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    current_time = now or datetime.now(UTC)
    timestamp = current_time.isoformat()
    clean_agent_id = clean_lobby_text(agent_id, limit=64)
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    if not clean_agent_id:
        raise ValueError("Agent id is required.")

    with LIVE_AGENT_STATE_LOCK:
        state = _read_state(output_root)
        agents = _agent_entries(state)
        for index, existing in enumerate(agents):
            if existing.get("agent_id") != clean_agent_id:
                continue
            agent = _without_output_only_freshness(existing)
            if clean_meeting_id and clean_lobby_text(agent.get("meeting_id"), limit=128) not in {"", clean_meeting_id}:
                raise ValueError(f"Live agent {clean_agent_id} is not attached to meeting {clean_meeting_id}.")
            agent["meeting_id"] = ""
            agent["status"] = "offline"
            agent["updated_at"] = timestamp
            agents[index] = agent
            _write_state(output_root, {"agents": agents})
            return agent
    raise ValueError(f"Live agent {clean_agent_id} was not found.")


def delete_live_agent(
    output_root: Path,
    agent_id: str,
) -> dict[str, object]:
    clean_agent_id = clean_lobby_text(agent_id, limit=64)
    if not clean_agent_id:
        raise ValueError("Agent id is required.")

    with LIVE_AGENT_STATE_LOCK:
        state = _read_state(output_root)
        agents = _agent_entries(state)
        kept = [agent for agent in agents if agent.get("agent_id") != clean_agent_id]
        if len(kept) == len(agents):
            raise ValueError(f"Live agent {clean_agent_id} was not found.")
        removed = next(dict(agent) for agent in agents if agent.get("agent_id") == clean_agent_id)
        _write_state(output_root, {"agents": kept})
        return _without_output_only_freshness(removed)


def _connection_endpoint(
    payload: dict[str, object],
    existing: dict[str, object],
    connection_kind: str,
) -> str:
    if connection_kind != "remote_bridge":
        return ""
    return clean_lobby_text(payload.get("endpoint"), limit=240) or clean_lobby_text(existing.get("endpoint"), limit=240)


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
        normalized_status = _normalize_persisted_status(status)
        if existing:
            agent = _without_output_only_freshness(existing)
            agent["status"] = _heartbeat_status(existing, requested_status=normalized_status, metadata=metadata)
            agent["updated_at"] = timestamp
            agent["last_seen_at"] = timestamp
        else:
            agent = {
                "agent_id": clean_agent_id,
                "display_name": clean_agent_id,
                "provider_kind": "manual",
                "connection_kind": "manual",
                "join_semantics": "manual_room_loop",
                "context_durability": "external_owner_managed",
                "sandbox_enforcement": "advisory",
                "status": normalized_status,
                "engagement_mode": "mentioned",
                "meeting_id": "",
                "session_id": "",
                "endpoint": "",
                "capabilities": [],
                "last_error": "",
                "last_attention": "",
                "last_reply_at": "",
                "last_observed_event_id": "",
                "last_observed_live_event_id": "",
                "last_observed_dm_event_id": "",
                "diagnostic": _bool_value(metadata.get("diagnostic")),
                "created_at": timestamp,
                "updated_at": timestamp,
                "last_seen_at": timestamp,
            }
        for key, limit in (
            ("session_id", 128),
            ("last_error", 500),
            ("last_attention", 128),
            ("last_reply_at", 64),
            ("last_observed_event_id", 128),
            ("last_observed_live_event_id", 128),
            ("last_observed_dm_event_id", 128),
        ):
            if key in metadata:
                if key == "last_error":
                    agent[key] = _clean_presence_last_error(metadata.get(key))
                elif key == "last_attention":
                    agent[key] = _clean_presence_attention(metadata.get(key))
                else:
                    agent[key] = clean_lobby_text(metadata.get(key), limit=limit)
        if any(key in metadata for key in LIVE_AGENT_QUOTA_FIELDS):
            previous_quota = {key: agent.get(key) for key in LIVE_AGENT_QUOTA_FIELDS if key in agent}
            for key in LIVE_AGENT_QUOTA_FIELDS:
                agent.pop(key, None)
            agent.update(clean_live_agent_quota_fields(metadata, previous_quota))
        if "diagnostic" in metadata:
            agent["diagnostic"] = _bool_value(metadata.get("diagnostic"))
        if (
            agent.get("status") == "online"
            and normalized_status == "online"
            and "last_error" not in metadata
        ):
            agent["last_error"] = ""
        agent.update(live_agent_context_contract(agent.get("provider_kind"), agent.get("connection_kind")))
        _upsert_agent(agents, agent)
        _write_state(output_root, {"agents": agents})
        return agent


def _heartbeat_status(
    existing: dict[str, object],
    *,
    requested_status: str,
    metadata: dict[str, object],
) -> str:
    current_status = clean_lobby_text(existing.get("status"), limit=32)
    if (
        requested_status == "online"
        and _bool_value(metadata.get("preserve_status"))
        and clean_lobby_text(existing.get("connection_kind"), limit=64) == "self_service"
        and "last_error" not in metadata
        and current_status in {"working", "error"}
    ):
        return current_status
    return requested_status


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
    writable = dict(state)
    writable["agents"] = [_without_output_only_freshness(agent) for agent in _agent_entries(state)]
    temp_path.write_text(json.dumps(writable, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)


def _agent_entries(state: dict[str, Any]) -> list[dict[str, object]]:
    agents = state.get("agents")
    if not isinstance(agents, list):
        return []
    return [agent for agent in agents if isinstance(agent, dict)]


def _operator_engagement_mode(agent: dict[str, object]) -> str:
    mode = clean_lobby_text(agent.get("engagement_mode"), limit=64)
    updated_at = clean_lobby_text(agent.get("engagement_mode_updated_at"), limit=64)
    if updated_at and mode in ENGAGEMENT_MODES:
        return mode
    return ""


def _live_agent_poll_interval_fields(
    payload: dict[str, object],
    existing: dict[str, object],
) -> dict[str, object]:
    if "poll_interval" in payload:
        return {
            "poll_interval": _clean_live_agent_poll_interval(payload.get("poll_interval")),
            "poll_interval_updated_at": clean_lobby_text(existing.get("poll_interval_updated_at"), limit=64),
        }
    if "poll_interval" in existing:
        fields = {"poll_interval": _clean_live_agent_poll_interval(existing.get("poll_interval"))}
        updated_at = clean_lobby_text(existing.get("poll_interval_updated_at"), limit=64)
        if updated_at:
            fields["poll_interval_updated_at"] = updated_at
        return fields
    return {}


def _clean_live_agent_poll_interval(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("Live agent poll_interval must be a finite non-negative number.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Live agent poll_interval must be a finite non-negative number.") from error
    if not isfinite(parsed) or parsed < 0:
        raise ValueError("Live agent poll_interval must be a finite non-negative number.")
    return parsed


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
    visible = _without_output_only_freshness(agent)
    visible.update(live_agent_context_contract(visible.get("provider_kind"), visible.get("connection_kind")))
    stale_after = max(0, int(stale_after_seconds))
    visible["stale_after_seconds"] = stale_after
    last_seen = _parse_timestamp(visible.get("last_seen_at"))
    if last_seen is not None:
        heartbeat_age = max(0.0, (now - last_seen).total_seconds())
        heartbeat_age_seconds = int(ceil(heartbeat_age))
        visible["heartbeat_age_seconds"] = heartbeat_age_seconds
    else:
        heartbeat_age_seconds = None
    status = clean_lobby_text(visible.get("status"), limit=32)
    if status not in {"online", "working"}:
        return visible
    if heartbeat_age_seconds is None:
        return visible
    if heartbeat_age_seconds > stale_after:
        visible["status"] = "stale"
    return visible


def _without_output_only_freshness(agent: dict[str, object]) -> dict[str, object]:
    clean_agent = {key: value for key, value in agent.items() if key not in OUTPUT_ONLY_FRESHNESS_FIELDS}
    if "last_error" in clean_agent:
        clean_agent["last_error"] = _clean_presence_last_error(clean_agent.get("last_error"))
    if "last_attention" in clean_agent:
        clean_agent["last_attention"] = _clean_presence_attention(clean_agent.get("last_attention"))
    return clean_agent


def _with_frontend_session_registration(output_root: Path, agent: dict[str, object]) -> dict[str, object]:
    if agent.get("process_group_id") and agent.get("live_agent_config_path"):
        return agent
    agent_id = clean_lobby_text(agent.get("agent_id"), limit=64)
    if not agent_id:
        return agent
    config_path = output_root / "live-agent-created" / f"{_safe_live_agent_filename(agent_id)}.json"
    if not config_path.exists() or not _config_file_owned_by_agent(config_path, agent_id):
        return agent
    updated = dict(agent)
    if not updated.get("process_group_id"):
        updated["process_group_id"] = f"agent-{agent_id}"
    if not updated.get("live_agent_config_path"):
        updated["live_agent_config_path"] = str(config_path)
    return updated


def _safe_live_agent_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-") or "live-agent"


def _config_file_owned_by_agent(path: Path, agent_id: str) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    agents = data.get("agents") if isinstance(data, dict) else None
    if not isinstance(agents, list) or len(agents) != 1 or not isinstance(agents[0], dict):
        return False
    return str(agents[0].get("agent_id") or "") == agent_id


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


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


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


def _clean_presence_last_error(value: object) -> str:
    text = clean_lobby_text(value, limit=500)
    if not text:
        return ""
    if _looks_sensitive_presence_error(text):
        return PRESENCE_ERROR_REDACTED
    return text


def _clean_presence_attention(value: object) -> str:
    text = clean_lobby_text(value, limit=128)
    if not text:
        return ""
    if text in SAFE_PRESENCE_ATTENTION_CODES:
        return text
    return PRESENCE_ATTENTION_REDACTED


def _looks_sensitive_presence_error(text: str) -> bool:
    if any(pattern.search(text) for pattern in SENSITIVE_PRESENCE_ERROR_MARKERS):
        return True
    return any(pattern.search(text) for pattern in SENSITIVE_PRESENCE_ERROR_PATTERNS)
