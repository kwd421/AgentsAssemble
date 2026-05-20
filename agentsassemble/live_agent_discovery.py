from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
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


def build_discovered_session_bundle(config: dict[str, Any]) -> dict[str, Any]:
    agents = [agent for agent in config.get("agents", []) if isinstance(agent, dict)]
    roles = []
    providers = []
    bindings = []
    for agent in agents:
        agent_id = str(agent.get("agent_id") or "").strip()
        if not agent_id:
            continue
        display_name = str(agent.get("display_name") or agent_id)
        provider_kind = str(agent.get("provider_kind") or "local_cli")
        role_id = _session_role_id(agent_id)
        provider_id = f"{agent_id}-provider"
        roles.append(
            {
                "id": role_id,
                "display_name": display_name,
                "lens": f"Live resident perspective from {display_name}.",
                "research_focus": "Join the resident session through the discovered local CLI transport.",
            }
        )
        provider: dict[str, Any] = {
            "id": provider_id,
            "kind": provider_kind,
            "display_name": display_name,
            "default_model": agent_id,
        }
        if agent.get("timeout_seconds"):
            provider["timeout_seconds"] = agent.get("timeout_seconds")
        providers.append(provider)
        bindings.append(
            {
                "agent_id": agent_id,
                "role_id": role_id,
                "owner_id": "discovery",
                "provider_id": provider_id,
                "model_id": agent_id,
                "permission_profile_id": "discovered_meeting_readonly",
                "join_mode": "fresh",
            }
        )
    return {
        "council_config": {
            "topic": "Discovered Live Agent Session",
            "question": "What should the discovered resident agents contribute from their own local CLI sessions?",
            "roles": roles,
            "meeting_template": {
                "id": "discovered_live_agents",
                "display_name": "Discovered Live Agents",
                "rounds": [
                    {
                        "id": "discovered_intro",
                        "title": "Discovered Agent Check-in",
                        "instruction": "Reply with one concise status update from your resident session.",
                        "turn_control": {"selection": "all_roles"},
                    }
                ],
            },
        },
        "agent_config": {
            "providers": providers,
            "permission_profiles": [
                {
                    "id": "discovered_meeting_readonly",
                    "meeting_read": True,
                    "lobby_chat": True,
                    "official_turn": True,
                    "web_search": False,
                    "tool_use": False,
                    "filesystem_read": False,
                    "filesystem_write": False,
                    "git_write": False,
                    "push": False,
                    "secrets": False,
                    "implementation": False,
                }
            ],
            "agent_bindings": bindings,
        },
    }


def discovered_session_bundle_paths(
    live_agent_output: Path,
    *,
    council_output: str = "",
    agent_output: str = "",
) -> tuple[Path, Path]:
    return (
        Path(council_output) if council_output else live_agent_output.with_name(_discovered_companion_name(live_agent_output, "council")),
        Path(agent_output) if agent_output else live_agent_output.with_name(_discovered_companion_name(live_agent_output, "agents")),
    )


def validate_distinct_session_bundle_paths(*paths: Path) -> None:
    seen: dict[Path, Path] = {}
    for path in paths:
        normalized = Path(path).expanduser().resolve(strict=False)
        if normalized in seen:
            raise ValueError("session bundle output paths must be distinct.")
        seen[normalized] = path


def add_session_bundle_outputs(
    report: dict[str, Any],
    *,
    live_agent_output: str,
    council_output: str,
    agent_output: str,
    server: str,
    meeting_id: str,
    group_id: str,
) -> None:
    report["session_bundle"] = {
        "live_agent_config_path": live_agent_output,
        "council_config_path": council_output,
        "agent_config_path": agent_output,
    }
    next_commands = report.setdefault("next_commands", {})
    if isinstance(next_commands, dict):
        command = [
            "python3",
            "-m",
            "agentsassemble.cli",
            "live-agent",
            "ensure-session",
            "--server",
            server,
        ]
        if meeting_id:
            command.extend(["--meeting-id", meeting_id])
        command.extend(
            [
                "--group-id",
                group_id,
                "--council-config",
                council_output,
                "--agent-config",
                agent_output,
                "--live-agent-config",
                live_agent_output,
            ]
        )
        next_commands["ensure_session"] = command


def _discovered_companion_name(path: Path, prefix: str) -> str:
    name = path.name
    if name.startswith("live-agents."):
        return f"{prefix}.{name[len('live-agents.'):]}"
    if name == "live-agents.json":
        return f"{prefix}.json"
    return f"{path.stem}.{prefix}{path.suffix}"


def _session_role_id(agent_id: str) -> str:
    cleaned = "".join(character if character.isalnum() else "_" for character in agent_id).strip("_").lower()
    if not cleaned:
        return "discovered_agent"
    if cleaned[0].isdigit():
        return f"agent_{cleaned}"
    return cleaned


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
