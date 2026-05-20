from __future__ import annotations

import shutil
from collections.abc import Callable
from typing import Any


DEFAULT_DISCOVERY_SERVER = "http://127.0.0.1:8765"


def build_discovered_live_agent_config(
    *,
    server: str = DEFAULT_DISCOVERY_SERVER,
    meeting_id: str = "",
    engagement_mode: str = "mentioned",
    include_legacy_gemini: bool = False,
    command_resolver: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    resolver = command_resolver or shutil.which
    discoveries = []
    agents = []
    for spec in _candidate_specs():
        path = resolver(spec["command"])
        available = bool(path)
        included = available and (not spec.get("legacy") or include_legacy_gemini)
        reason = "included" if included else _discovery_skip_reason(available=available, legacy=bool(spec.get("legacy")))
        discoveries.append(
            {
                "command": spec["command"],
                "provider_kind": spec["provider_kind"],
                "connection_kind": spec["connection_kind"],
                "available": available,
                "included": included,
                "path": path or "",
                "reason": reason,
            }
        )
        if included:
            agents.append(_agent_entry(spec, meeting_id=meeting_id, engagement_mode=engagement_mode))
    config = {
        "server": server,
        "poll_interval": 2,
        "heartbeat_interval": 30,
        "cooldown": 5,
        "max_chain_depth": 1,
        "agents": agents,
    }
    return {
        "status": "ok" if agents else "empty",
        "config": config,
        "discoveries": discoveries,
        "next_commands": _next_commands(server=server),
    }


def _candidate_specs() -> list[dict[str, Any]]:
    return [
        {
            "command": "claude",
            "agent_id": "claude-code-live",
            "display_name": "Claude Code",
            "provider_kind": "claude_code",
            "connection_kind": "terminal_session",
            "terminal_idle_timeout": 0.75,
            "timeout_seconds": 120,
        },
        {
            "command": "codex",
            "agent_id": "codex-live",
            "display_name": "Codex",
            "provider_kind": "codex_live_session",
            "connection_kind": "live_session",
            "timeout_seconds": 240,
            "omit_command": True,
        },
        {
            "command": "antigravity",
            "agent_id": "antigravity-cli-live",
            "display_name": "Antigravity CLI",
            "provider_kind": "antigravity_cli",
            "connection_kind": "self_service",
            "timeout_seconds": 120,
        },
        {
            "command": "gemini",
            "agent_id": "gemini-cli-legacy-live",
            "display_name": "Gemini CLI Legacy",
            "provider_kind": "gemini_cli_legacy",
            "connection_kind": "terminal_session",
            "terminal_idle_timeout": 0.75,
            "timeout_seconds": 120,
            "legacy": True,
        },
    ]


def _agent_entry(spec: dict[str, Any], *, meeting_id: str, engagement_mode: str) -> dict[str, Any]:
    entry = {
        "agent_id": spec["agent_id"],
        "display_name": spec["display_name"],
        "provider_kind": spec["provider_kind"],
        "connection_kind": spec["connection_kind"],
        "meeting_id": meeting_id,
        "engagement_mode": engagement_mode,
        "timeout_seconds": spec["timeout_seconds"],
    }
    if not spec.get("omit_command"):
        entry["command"] = [spec["command"]]
    if "terminal_idle_timeout" in spec:
        entry["terminal_idle_timeout"] = spec["terminal_idle_timeout"]
    return entry


def _discovery_skip_reason(*, available: bool, legacy: bool) -> str:
    if not available:
        return "not_found"
    if legacy:
        return "legacy"
    return "not_included"


def _next_commands(*, server: str) -> dict[str, list[str]]:
    return {
        "preflight": [
            "python3",
            "-m",
            "agentsassemble.cli",
            "live-agent",
            "preflight",
            "--config",
            "<output>",
        ],
        "run_group": [
            "python3",
            "-m",
            "agentsassemble.cli",
            "live-agent",
            "run-group",
            "--server",
            server,
            "--config",
            "<output>",
        ],
    }


def fill_discovery_next_command_output(report: dict[str, Any], output: str) -> None:
    next_commands = report.get("next_commands")
    if not isinstance(next_commands, dict):
        return
    for command in next_commands.values():
        if not isinstance(command, list):
            continue
        for index, part in enumerate(command):
            if part == "<output>":
                command[index] = output
