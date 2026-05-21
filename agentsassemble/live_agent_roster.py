from __future__ import annotations

import math

from agentsassemble.live_agents import PRESENCE_ERROR_REDACTED, _looks_sensitive_presence_error
from agentsassemble.meeting_events import clean_lobby_text


SAFE_LIVE_AGENT_ROSTER_FIELDS = (
    "agent_id",
    "display_name",
    "provider_kind",
    "connection_kind",
    "status",
    "meeting_id",
    "engagement_mode",
    "engagement_mode_updated_at",
    "last_seen_at",
    "last_error",
    "last_reply_at",
    "last_observed_event_id",
    "last_observed_live_event_id",
    "heartbeat_age_seconds",
    "stale_after_seconds",
    "capabilities",
)
SAFE_LIVE_AGENT_ROSTER_NUMERIC_FIELDS = {"heartbeat_age_seconds", "stale_after_seconds"}


def filter_live_agent_roster(
    agents: list[dict[str, object]],
    *,
    meeting_id: str = "",
    agent_ids: list[str] | None = None,
    statuses: list[str] | None = None,
) -> list[dict[str, object]]:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    clean_agent_ids = {
        clean_lobby_text(agent_id, limit=64)
        for agent_id in agent_ids or []
        if clean_lobby_text(agent_id, limit=64)
    }
    clean_statuses = {
        clean_lobby_text(status, limit=32)
        for status in statuses or []
        if clean_lobby_text(status, limit=32)
    }
    filtered = []
    for agent in agents:
        if clean_meeting_id and str(agent.get("meeting_id") or "") != clean_meeting_id:
            continue
        if clean_agent_ids and str(agent.get("agent_id") or "") not in clean_agent_ids:
            continue
        if clean_statuses and str(agent.get("status") or "") not in clean_statuses:
            continue
        filtered.append(agent)
    return filtered


def safe_live_agent_roster_payload(payload: dict[str, object]) -> dict[str, object]:
    agents = payload.get("agents") if isinstance(payload.get("agents"), list) else []
    return {
        "agents": [
            safe_live_agent_roster_agent(item)
            for item in agents
            if isinstance(item, dict)
        ]
    }


def safe_live_agent_roster_agent(agent: dict[str, object]) -> dict[str, object]:
    safe_agent: dict[str, object] = {}
    for field in SAFE_LIVE_AGENT_ROSTER_FIELDS:
        if field not in agent:
            continue
        value = agent.get(field)
        if field in SAFE_LIVE_AGENT_ROSTER_NUMERIC_FIELDS:
            safe_agent[field] = safe_live_agent_roster_number(value)
        elif field == "capabilities":
            safe_agent[field] = safe_live_agent_roster_capabilities(value)
        elif field == "last_error":
            safe_agent[field] = safe_live_agent_roster_error(value)
        else:
            safe_agent[field] = safe_live_agent_roster_text(value, limit=_live_agent_roster_field_limit(field))
    return safe_agent


def safe_live_agent_roster_capabilities(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    capabilities = []
    for item in value:
        capability = safe_live_agent_roster_text(item, limit=64)
        if capability:
            capabilities.append(capability)
    return capabilities


def safe_live_agent_roster_error(value: object) -> str:
    text = clean_lobby_text(value, limit=500)
    if not text:
        return ""
    if _looks_sensitive_presence_error(text):
        return PRESENCE_ERROR_REDACTED
    return text


def safe_live_agent_roster_text(value: object, *, limit: int, default: str = "") -> str:
    text = clean_lobby_text(value, limit=limit)
    if not text:
        return default
    if _looks_sensitive_presence_error(text):
        return "[redacted]"
    return text


def safe_live_agent_roster_number(value: object) -> int | float:
    number = _safe_nonnegative_float(value)
    return int(number) if number.is_integer() else number


def _live_agent_roster_field_limit(field: str) -> int:
    if field in {"agent_id", "provider_kind", "connection_kind", "status", "engagement_mode"}:
        return 64
    if field in {"display_name", "meeting_id", "last_observed_event_id", "last_observed_live_event_id"}:
        return 128
    return 240


def _safe_nonnegative_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number >= 0 else 0.0
