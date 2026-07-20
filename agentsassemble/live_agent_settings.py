from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from agentsassemble.legacy.meeting.core.events import clean_lobby_text


def update_live_agent_config_poll_interval(
    config_path: Path,
    agent_id: str,
    poll_interval: object,
    cooldown: object | None = None,
) -> dict[str, object]:
    clean_agent_id = clean_lobby_text(agent_id, limit=64)
    if not clean_agent_id:
        raise ValueError("Agent id is required.")
    parsed_poll_interval = _nonnegative_poll_interval(poll_interval)
    parsed_cooldown = None if cooldown is None else _nonnegative_number(cooldown, "cooldown")

    data = _read_config(config_path)
    agents = data.get("agents")
    if not isinstance(agents, list) or not agents:
        raise ValueError("Live agent group config requires a non-empty agents list.")

    updated = False
    for agent in agents:
        if not isinstance(agent, dict):
            raise ValueError("Each live agent entry must be a JSON object.")
        if clean_lobby_text(agent.get("agent_id"), limit=64) != clean_agent_id:
            continue
        agent["poll_interval"] = parsed_poll_interval
        if parsed_cooldown is not None:
            agent["cooldown"] = parsed_cooldown
        updated = True

    if not updated:
        raise ValueError(f"Live agent config does not include {clean_agent_id}.")

    _write_config(config_path, data)
    return {
        "config_path": str(config_path),
        "agent_id": clean_agent_id,
        "poll_interval": parsed_poll_interval,
        **({"cooldown": parsed_cooldown} if parsed_cooldown is not None else {}),
    }


def update_live_agent_config_options(
    config_path: Path,
    agent_id: str,
    *,
    permission_option: object | None = None,
    fast_mode: object | None = None,
) -> dict[str, object]:
    """Update permission_option / fast_mode for one agent in a saved group config.

    Either field may be None to leave it unchanged. The new values take effect the
    next time the agent is launched (RESUME/START)."""
    clean_agent_id = clean_lobby_text(agent_id, limit=64)
    if not clean_agent_id:
        raise ValueError("Agent id is required.")
    new_permission = None if permission_option is None else clean_lobby_text(permission_option, limit=64)

    data = _read_config(config_path)
    agents = data.get("agents")
    if not isinstance(agents, list) or not agents:
        raise ValueError("Live agent group config requires a non-empty agents list.")

    updated = False
    applied: dict[str, object] = {}
    for agent in agents:
        if not isinstance(agent, dict):
            raise ValueError("Each live agent entry must be a JSON object.")
        if clean_lobby_text(agent.get("agent_id"), limit=64) != clean_agent_id:
            continue
        if new_permission is not None:
            if new_permission:
                agent["permission_option"] = new_permission
            else:
                agent.pop("permission_option", None)
            applied["permission_option"] = new_permission
        if fast_mode is not None:
            if bool(fast_mode):
                agent["fast_mode"] = True
            else:
                agent.pop("fast_mode", None)
            applied["fast_mode"] = bool(fast_mode)
        updated = True

    if not updated:
        raise ValueError(f"Live agent config does not include {clean_agent_id}.")

    _write_config(config_path, data)
    return {"config_path": str(config_path), "agent_id": clean_agent_id, **applied}


def _read_config(config_path: Path) -> dict[str, Any]:
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"Live agent config was not found: {config_path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Live agent config is not valid JSON: {config_path}") from error
    if not isinstance(data, dict):
        raise ValueError("Live agent group config must be a JSON object.")
    return data


def _write_config(config_path: Path, data: dict[str, Any]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _nonnegative_poll_interval(value: object) -> float:
    return _nonnegative_number(value, "poll_interval")


def _nonnegative_number(value: object, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"Live agent {field_name} must be a finite non-negative number.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Live agent {field_name} must be a finite non-negative number.") from error
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"Live agent {field_name} must be a finite non-negative number.")
    return parsed
