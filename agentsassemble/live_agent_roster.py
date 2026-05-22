from __future__ import annotations

import math

from agentsassemble.live_agent_context import (
    live_agent_context_contract,
    safe_live_agent_context_durability,
    safe_live_agent_join_semantics,
)
from agentsassemble.live_agents import PRESENCE_ERROR_REDACTED, _looks_sensitive_presence_error
from agentsassemble.meeting_events import clean_lobby_text


SAFE_LIVE_AGENT_ROSTER_FIELDS = (
    "agent_id",
    "display_name",
    "provider_kind",
    "connection_kind",
    "join_semantics",
    "context_durability",
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
    "admission_status",
    "host_approved_binding",
    "binding_role_id",
    "binding_provider_id",
    "binding_provider_kind",
    "binding_permission_profile_id",
    "binding_join_mode",
    "binding_conflicts",
    "admission_evidence_source",
)
SAFE_LIVE_AGENT_ROSTER_NUMERIC_FIELDS = {"heartbeat_age_seconds", "stale_after_seconds"}
SAFE_LIVE_AGENT_ADMISSION_STATUSES = {
    "lobby_only",
    "meeting_missing",
    "meeting_lobby_only",
    "bound_to_meeting",
    "binding_conflict",
}
SAFE_LIVE_AGENT_BINDING_CONFLICTS = {
    "binding_provider_missing",
    "provider_kind_mismatch",
}
SAFE_LIVE_AGENT_ADMISSION_EVIDENCE_SOURCES = {"meeting_record"}


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
    context_contract = live_agent_context_contract(agent.get("provider_kind"), agent.get("connection_kind"))
    admission_evidence_source = safe_live_agent_admission_evidence_source(agent.get("admission_evidence_source"))
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
        elif field == "admission_status":
            safe_agent[field] = safe_live_agent_admission_status(value)
        elif field == "host_approved_binding":
            safe_agent[field] = value is True
        elif field == "binding_conflicts":
            safe_agent[field] = safe_live_agent_binding_conflicts(value)
        elif field == "admission_evidence_source":
            safe_agent[field] = admission_evidence_source
        elif field == "join_semantics":
            safe_agent[field] = safe_live_agent_join_semantics(context_contract["join_semantics"])
        elif field == "context_durability":
            safe_agent[field] = safe_live_agent_context_durability(context_contract["context_durability"])
        else:
            safe_agent[field] = safe_live_agent_roster_text(value, limit=_live_agent_roster_field_limit(field))
    if admission_evidence_source != "meeting_record":
        for admission_field in (
            "admission_status",
            "host_approved_binding",
            "binding_role_id",
            "binding_provider_id",
            "binding_provider_kind",
            "binding_permission_profile_id",
            "binding_join_mode",
            "binding_conflicts",
            "admission_evidence_source",
        ):
            safe_agent.pop(admission_field, None)
    if safe_agent.get("admission_status") != "bound_to_meeting" and "host_approved_binding" in safe_agent:
        safe_agent["host_approved_binding"] = False
    if safe_agent.get("admission_status") not in {"bound_to_meeting", "binding_conflict"}:
        for binding_field in (
            "binding_role_id",
            "binding_provider_id",
            "binding_provider_kind",
            "binding_permission_profile_id",
            "binding_join_mode",
            "binding_conflicts",
        ):
            safe_agent.pop(binding_field, None)
    if "join_semantics" not in safe_agent and ("provider_kind" in agent or "connection_kind" in agent):
        safe_agent["join_semantics"] = safe_live_agent_join_semantics(context_contract["join_semantics"])
    if "context_durability" not in safe_agent and ("provider_kind" in agent or "connection_kind" in agent):
        safe_agent["context_durability"] = safe_live_agent_context_durability(context_contract["context_durability"])
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


def safe_live_agent_admission_status(value: object) -> str:
    text = clean_lobby_text(value, limit=64)
    return text if text in SAFE_LIVE_AGENT_ADMISSION_STATUSES else ""


def safe_live_agent_admission_evidence_source(value: object) -> str:
    text = clean_lobby_text(value, limit=64)
    return text if text in SAFE_LIVE_AGENT_ADMISSION_EVIDENCE_SOURCES else ""


def safe_live_agent_binding_conflicts(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    conflicts = []
    for item in value:
        conflict = clean_lobby_text(item, limit=64)
        if conflict in SAFE_LIVE_AGENT_BINDING_CONFLICTS:
            conflicts.append(conflict)
    return conflicts


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
    if field in {
        "agent_id",
        "provider_kind",
        "connection_kind",
        "join_semantics",
        "context_durability",
        "status",
        "engagement_mode",
        "admission_status",
        "binding_provider_kind",
        "binding_join_mode",
        "admission_evidence_source",
    }:
        return 64
    if field in {
        "display_name",
        "meeting_id",
        "last_observed_event_id",
        "last_observed_live_event_id",
        "binding_role_id",
        "binding_provider_id",
        "binding_permission_profile_id",
    }:
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
