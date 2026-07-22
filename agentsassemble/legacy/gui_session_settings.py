"""Editable settings for retained GUI Agent Session participants."""

from __future__ import annotations

from pathlib import Path

from agentsassemble.legacy.live_agent.runtime.settings import (
    update_live_agent_config_options,
    update_live_agent_config_poll_interval,
)
from agentsassemble.legacy.live_agent.state import (
    read_live_agents,
    update_live_agent_cooldown,
    update_live_agent_options,
    update_live_agent_poll_interval,
)
from agentsassemble.legacy.meeting.core.events import clean_lobby_text


def agent_timing_payload(
    output_root: Path,
    payload: dict[str, object],
) -> dict[str, object]:
    agent_id = _required_agent_id(output_root, payload)
    live_agent_config_path = str(
        payload.get("live_agent_config_path") or payload.get("live_agent_config") or "",
    ).strip()
    config_result: dict[str, object] = {}
    if live_agent_config_path:
        config_result = update_live_agent_config_poll_interval(
            Path(live_agent_config_path),
            agent_id,
            payload.get("poll_interval"),
            payload.get("cooldown") if "cooldown" in payload else None,
        )
    agent = update_live_agent_poll_interval(
        output_root,
        agent_id,
        payload.get("poll_interval"),
    )
    if "cooldown" in payload:
        agent = update_live_agent_cooldown(
            output_root,
            agent_id,
            payload.get("cooldown"),
        )
    return {
        "status": "updated",
        "agent_id": agent_id,
        "poll_interval": agent.get("poll_interval"),
        "cooldown": agent.get("cooldown"),
        "config_path": str(config_result.get("config_path") or live_agent_config_path),
        "agent": agent,
    }


def agent_options_payload(
    output_root: Path,
    payload: dict[str, object],
) -> dict[str, object]:
    """Persist permission and fast-mode settings for the next launch."""

    agent_id = _required_agent_id(output_root, payload)
    has_permission = "permission_option" in payload
    has_fast = "fast_mode" in payload
    if not has_permission and not has_fast:
        raise ValueError(
            "Nothing to update: provide permission_option and/or fast_mode.",
        )
    permission_option = payload.get("permission_option") if has_permission else None
    fast_mode = payload.get("fast_mode") if has_fast else None
    live_agent_config_path = str(
        payload.get("live_agent_config_path") or payload.get("live_agent_config") or "",
    ).strip()
    config_result: dict[str, object] = {}
    if live_agent_config_path:
        config_result = update_live_agent_config_options(
            Path(live_agent_config_path),
            agent_id,
            permission_option=permission_option,
            fast_mode=fast_mode,
        )
    agent = update_live_agent_options(
        output_root,
        agent_id,
        permission_option=permission_option,
        fast_mode=fast_mode,
    )
    return {
        "status": "updated",
        "agent_id": agent_id,
        "permission_option": agent.get("permission_option"),
        "fast_mode": bool(agent.get("fast_mode")),
        "config_path": str(config_result.get("config_path") or live_agent_config_path),
        "applies_on": "next_start",
        "agent": agent,
    }


def _required_agent_id(
    output_root: Path,
    payload: dict[str, object],
) -> str:
    agent_id = clean_lobby_text(payload.get("agent_id"), limit=64)
    if not agent_id:
        raise ValueError("Agent id is required.")
    if not any(
        str(agent.get("agent_id") or "") == agent_id
        for agent in read_live_agents(output_root)
    ):
        raise ValueError(f"Live agent {agent_id} was not found.")
    return agent_id
