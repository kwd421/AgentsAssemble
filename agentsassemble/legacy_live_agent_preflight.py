"""Configuration-only preflight for retained resident agents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentsassemble.live_agent_preflight import preflight_live_agent_config


@dataclass(frozen=True)
class LegacyLiveAgentPreflightService:
    def run(self, payload: dict[str, object], *, default_server: str) -> dict[str, object]:
        return live_agent_preflight_payload(payload, default_server=default_server)


def live_agent_preflight_payload(
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    config_path = Path(str(payload.get("config_path") or "configs/live-agents.example.json"))
    server = str(payload.get("server") or default_server)
    return preflight_live_agent_config(config_path, server_override=server)
