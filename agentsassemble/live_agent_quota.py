from __future__ import annotations

import math
from collections.abc import Iterable

from agentsassemble.legacy.meeting.core.events import clean_lobby_text

LIVE_AGENT_QUOTA_FIELDS = ("quota_5h", "quota_1w", "quota_state", "quota_windows")
LIVE_AGENT_QUOTA_STATES = {"ok", "low", "exhausted", "unknown", ""}
LOCAL_OWNER_CONNECTION_KINDS = {
    "codex_resume",
    "local_cli",
    "live_session",
    "manual",
    "self_service",
    "terminal_session",
}
REMOTE_OWNER_CONNECTION_KINDS = {"native_remote_room_client", "remote_bridge"}


def clean_live_agent_quota_fields(
    source: dict[str, object],
    existing: dict[str, object] | None = None,
) -> dict[str, object]:
    existing = existing or {}
    fields: dict[str, object] = {}
    for key in ("quota_5h", "quota_1w"):
        if key in source:
            value = clean_lobby_text(source.get(key), limit=64)
        else:
            value = clean_lobby_text(existing.get(key), limit=64)
        if value:
            fields[key] = value

    if "quota_state" in source:
        state = clean_live_agent_quota_state(source.get("quota_state"))
    else:
        state = clean_live_agent_quota_state(existing.get("quota_state"))
    if state:
        fields["quota_state"] = state

    if "quota_windows" in source:
        windows = clean_live_agent_quota_windows(source.get("quota_windows"))
    else:
        windows = clean_live_agent_quota_windows(existing.get("quota_windows"))
    if windows:
        fields["quota_windows"] = windows
    return fields


def clean_live_agent_quota_state(value: object) -> str:
    state = clean_lobby_text(value, limit=32).lower()
    return state if state in LIVE_AGENT_QUOTA_STATES else "unknown"


def clean_live_agent_quota_windows(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    windows: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        label = clean_lobby_text(item.get("label"), limit=64)
        percent = _clean_quota_percent(item.get("percent"))
        if not label or percent is None:
            continue
        window: dict[str, object] = {"label": label, "percent": percent}
        reset = _clean_quota_reset(item.get("resetsAt"))
        if reset is not None:
            window["resetsAt"] = reset
        for key in ("used", "limit", "remaining"):
            numeric = _clean_quota_number(item.get(key))
            if numeric is not None:
                window[key] = numeric
        unit = clean_lobby_text(item.get("unit"), limit=24)
        if unit:
            window["unit"] = unit
        windows.append(window)
        if len(windows) >= 4:
            break
    return windows


def quota_viewer_for_host() -> dict[str, object]:
    return {"host_can_view_local_agent_quotas": True}


def quota_viewer_for_session(session: dict[str, object]) -> dict[str, object]:
    agent_id = clean_lobby_text(session.get("agent_id"), limit=128)
    owned_ids = [agent_id, f"{agent_id}-ai"] if agent_id else []
    return {"owned_agent_ids": owned_ids, "host_can_view_local_agent_quotas": False}


def quota_fields_for_viewer(
    agent: dict[str, object],
    viewer: dict[str, object] | None,
) -> dict[str, object]:
    if not can_view_agent_quota(agent, viewer):
        return {}
    return clean_live_agent_quota_fields(agent)


def can_view_agent_quota(agent: dict[str, object], viewer: dict[str, object] | None) -> bool:
    viewer = viewer or {}
    agent_id = clean_lobby_text(agent.get("agent_id"), limit=128)
    if not agent_id:
        return False
    if agent_id in _clean_id_set(viewer.get("owned_agent_ids")):
        return True
    if not bool(viewer.get("host_can_view_local_agent_quotas")):
        return False
    connection_kind = clean_lobby_text(agent.get("connection_kind"), limit=64)
    if connection_kind in REMOTE_OWNER_CONNECTION_KINDS:
        return False
    if agent_id in _clean_id_set(viewer.get("local_process_agent_ids")):
        return True
    return connection_kind in LOCAL_OWNER_CONNECTION_KINDS


def _clean_id_set(value: object) -> set[str]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return set()
    return {
        clean_lobby_text(item, limit=128)
        for item in value
        if clean_lobby_text(item, limit=128)
    }


def _clean_quota_percent(value: object) -> int | None:
    number = _clean_quota_number(value)
    if number is None:
        return None
    return int(max(0, min(100, round(number))))


def _clean_quota_number(value: object) -> float | int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    rounded = round(number, 2)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def _clean_quota_reset(value: object) -> str | int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value if math.isfinite(float(value)) else None
    text = clean_lobby_text(value, limit=64)
    return text or None
