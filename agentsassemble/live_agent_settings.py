from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from agentsassemble.meeting_events import clean_lobby_text


def update_live_agent_config_poll_interval(
    config_path: Path,
    agent_id: str,
    poll_interval: object,
) -> dict[str, object]:
    clean_agent_id = clean_lobby_text(agent_id, limit=64)
    if not clean_agent_id:
        raise ValueError("Agent id is required.")
    parsed_poll_interval = _nonnegative_poll_interval(poll_interval)

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
        updated = True

    if not updated:
        raise ValueError(f"Live agent config does not include {clean_agent_id}.")

    _write_config(config_path, data)
    return {
        "config_path": str(config_path),
        "agent_id": clean_agent_id,
        "poll_interval": parsed_poll_interval,
    }


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
    if isinstance(value, bool):
        raise ValueError("Live agent poll_interval must be a finite non-negative number.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Live agent poll_interval must be a finite non-negative number.") from error
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError("Live agent poll_interval must be a finite non-negative number.")
    return parsed
