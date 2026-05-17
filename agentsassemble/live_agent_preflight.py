from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path

from agentsassemble.live_agent_runner import (
    ResidentAgentConfig,
    SUPPORTED_RESIDENT_CONNECTION_KINDS,
    resident_connection_kind_error,
)
from agentsassemble.remote_bridge_config import remote_bridge_auth_ref_available, remote_bridge_endpoint_error


def preflight_live_agent_config(
    config_path: Path,
    *,
    server_override: str | None = None,
    command_resolver: Callable[[str], str | None] | None = None,
) -> dict[str, object]:
    resolver = command_resolver or _resolve_command_path
    try:
        configs = _load_preflight_configs(config_path, server_override=server_override)
    except Exception as error:
        return _failed_config_report(config_path, str(error))
    if not configs:
        return _failed_config_report(config_path, "Live agent group config did not contain any valid agent objects.")
    top_checks = [_duplicate_agent_id_check(configs)]
    agents = [_preflight_agent(config, resolver) for config in configs]
    checks_failed = _failed_check_count(top_checks) + sum(_failed_check_count(agent["checks"]) for agent in agents)
    failed_agents = sum(1 for agent in agents if agent["status"] == "failed")
    status = "failed" if checks_failed else "ok"
    return {
        "status": status,
        "config_path": str(config_path),
        "server": configs[0].server if configs else str(server_override or ""),
        "summary": {
            "agents": len(agents),
            "failed_agents": failed_agents,
            "checks_failed": checks_failed,
        },
        "checks": top_checks,
        "agents": agents,
    }


def _failed_config_report(config_path: Path, message: str) -> dict[str, object]:
    return {
        "status": "failed",
        "config_path": str(config_path),
        "server": "",
        "summary": {"agents": 0, "failed_agents": 0, "checks_failed": 1},
        "checks": [{"id": "config_load", "status": "failed", "message": message}],
        "agents": [],
    }


def _duplicate_agent_id_check(configs: list[ResidentAgentConfig]) -> dict[str, str]:
    counts: dict[str, int] = {}
    for config in configs:
        if config.agent_id:
            counts[config.agent_id] = counts.get(config.agent_id, 0) + 1
    duplicates = sorted(agent_id for agent_id, count in counts.items() if count > 1)
    if duplicates:
        return {
            "id": "agent_ids",
            "status": "failed",
            "message": f"Duplicate agent ids: {', '.join(duplicates)}",
        }
    return {"id": "agent_ids", "status": "ok", "message": "Agent ids are unique."}


def _preflight_agent(
    config: ResidentAgentConfig,
    command_resolver: Callable[[str], str | None],
) -> dict[str, object]:
    checks = [
        _agent_id_check(config.agent_id),
        _connection_kind_check(config.connection_kind),
    ]
    if config.connection_kind == "remote_bridge":
        checks.extend(
            [
                _remote_bridge_endpoint_check(config.endpoint),
                _remote_bridge_auth_ref_check(config.auth_ref),
            ]
        )
    else:
        checks.append(_command_check(config.command, command_resolver))
    status = "failed" if _failed_check_count(checks) else "ok"
    command_path = ""
    for check in checks:
        if check["id"] == "command" and check["status"] == "ok":
            command_path = check.get("path", "")
    return {
        "agent_id": config.agent_id,
        "display_name": config.display_name,
        "provider_kind": config.provider_kind,
        "connection_kind": config.connection_kind,
        "command": config.command,
        "command_path": command_path,
        "status": status,
        "checks": checks,
    }


def _load_preflight_configs(path: Path, *, server_override: str | None = None) -> list[ResidentAgentConfig]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Live agent group config must be a JSON object.")
    server = str(server_override or data.get("server") or "http://127.0.0.1:8765")
    defaults = {
        "poll_interval": float(data.get("poll_interval", 2.0)),
        "heartbeat_interval": float(data.get("heartbeat_interval", 30.0)),
        "cooldown": float(data.get("cooldown", 5.0)),
        "max_chain_depth": int(data.get("max_chain_depth", 1)),
        "max_ticks": int(data.get("max_ticks", 0)),
    }
    agents = data.get("agents")
    if not isinstance(agents, list) or not agents:
        raise ValueError("Live agent group config requires a non-empty agents list.")
    return [
        _preflight_config_from_mapping(agent, server=server, defaults=defaults, server_override=server_override)
        for agent in agents
        if isinstance(agent, dict)
    ]


def _preflight_config_from_mapping(
    data: dict[str, object],
    *,
    server: str,
    defaults: dict[str, int | float],
    server_override: str | None = None,
) -> ResidentAgentConfig:
    command = data.get("command")
    endpoint = data.get("endpoint")
    auth_ref = data.get("auth_ref")
    return ResidentAgentConfig(
        server=str(server_override or data.get("server") or server),
        agent_id=str(data.get("agent_id") or ""),
        display_name=str(data.get("display_name") or data.get("agent_id") or ""),
        provider_kind=str(data.get("provider_kind") or "local_cli"),
        connection_kind=str(data.get("connection_kind") or "local_cli"),
        session_id=str(data.get("session_id") or ""),
        endpoint=endpoint if isinstance(endpoint, str) else "",
        auth_ref=auth_ref if isinstance(auth_ref, str) else "",
        meeting_id=str(data.get("meeting_id") or ""),
        engagement_mode=str(data.get("engagement_mode") or "mentioned"),
        command=[str(part) for part in command] if isinstance(command, list) else [],
        timeout_seconds=int(data.get("timeout_seconds") or data.get("timeout") or 120),
        poll_interval=float(_value_or_default(data.get("poll_interval"), defaults["poll_interval"])),
        heartbeat_interval=float(_value_or_default(data.get("heartbeat_interval"), defaults["heartbeat_interval"])),
        cooldown=float(data.get("cooldown") if data.get("cooldown") is not None else defaults["cooldown"]),
        max_chain_depth=int(_value_or_default(data.get("max_chain_depth"), defaults["max_chain_depth"])),
        max_ticks=int(data.get("max_ticks") if data.get("max_ticks") is not None else defaults["max_ticks"]),
    )


def _value_or_default(value: object, default: object) -> object:
    return default if value is None else value


def _agent_id_check(agent_id: str) -> dict[str, str]:
    if agent_id:
        return {"id": "agent_id", "status": "ok", "message": "Agent id is present."}
    return {"id": "agent_id", "status": "failed", "message": "Agent id is required."}


def _connection_kind_check(connection_kind: str) -> dict[str, str]:
    if connection_kind in SUPPORTED_RESIDENT_CONNECTION_KINDS:
        return {
            "id": "connection_kind",
            "status": "ok",
            "message": f"Resident connection kind is {connection_kind}.",
        }
    return {
        "id": "connection_kind",
        "status": "failed",
        "message": resident_connection_kind_error(),
    }


def _command_check(command: list[str], command_resolver: Callable[[str], str | None]) -> dict[str, str]:
    executable = str(command[0] if command else "").strip()
    if not executable:
        return {"id": "command", "status": "failed", "message": "Command is empty."}
    resolved = command_resolver(executable)
    if resolved:
        return {
            "id": "command",
            "status": "ok",
            "message": f"Command found: {executable}",
            "path": resolved,
        }
    return {"id": "command", "status": "failed", "message": f"Command not found: {executable}"}


def _remote_bridge_endpoint_check(endpoint: str) -> dict[str, str]:
    error = remote_bridge_endpoint_error(endpoint)
    if not error:
        return {
            "id": "remote_bridge_endpoint",
            "status": "ok",
            "message": "Remote bridge endpoint is configured.",
        }
    return {
        "id": "remote_bridge_endpoint",
        "status": "failed",
        "message": error,
    }


def _remote_bridge_auth_ref_check(auth_ref: str) -> dict[str, str]:
    if remote_bridge_auth_ref_available(auth_ref):
        return {
            "id": "remote_bridge_auth_ref",
            "status": "ok",
            "message": "Remote bridge auth_ref is available.",
        }
    return {
        "id": "remote_bridge_auth_ref",
        "status": "failed",
        "message": "Remote bridge auth_ref is not available.",
    }


def _resolve_command_path(command: str) -> str | None:
    expanded = Path(command).expanduser()
    if os.sep in command or (os.altsep and os.altsep in command):
        return str(expanded) if expanded.is_file() and os.access(expanded, os.X_OK) else None
    return shutil.which(command)


def _failed_check_count(checks: list[dict[str, object]]) -> int:
    return sum(1 for check in checks if check.get("status") == "failed")
