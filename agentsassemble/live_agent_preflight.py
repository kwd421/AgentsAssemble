from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from subprocess import TimeoutExpired
from typing import Any

from agentsassemble.codex_resident import (
    codex_exec_prefix,
    codex_provider_connection_check,
    default_codex_resident_command,
)
from agentsassemble.cursor_resident import (
    cursor_command_check,
    cursor_provider_connection_check,
    default_cursor_resident_command,
)
from agentsassemble.grok_resident import (
    default_grok_resident_command,
    grok_command_check,
    grok_provider_connection_check,
)
from agentsassemble.kiro_resident import (
    default_kiro_resident_command,
    kiro_command_check,
    kiro_provider_connection_check,
)
from agentsassemble.live_session_transport import terminal_sessions_supported
from agentsassemble.live_agent_runner import (
    ResidentAgentConfig,
    SUPPORTED_RESIDENT_CONNECTION_KINDS,
    live_agent_command_parts,
    live_agent_nonnegative_float,
    live_agent_nonnegative_int,
    resident_connection_kind_error,
)
from agentsassemble.remote_bridge_config import remote_bridge_auth_ref_available, remote_bridge_endpoint_error
from agentsassemble.sandbox_launcher import sandbox_launcher_for


def preflight_live_agent_config(
    config_path: Path,
    *,
    server_override: str | None = None,
    command_resolver: Callable[[str], str | None] | None = None,
    codex_capability_checker: Callable[[list[str]], dict[str, str]] | None = None,
    codex_command_runner: Callable[..., Any] | None = None,
) -> dict[str, object]:
    resolver = command_resolver or _resolve_command_path
    codex_checker = codex_capability_checker or (
        lambda command: _codex_exec_safety_flags_check(command, command_runner=codex_command_runner)
    )
    try:
        configs = _load_preflight_configs(config_path, server_override=server_override)
    except Exception as error:
        return _failed_config_report(config_path, str(error))
    if not configs:
        return _failed_config_report(config_path, "Live agent group config did not contain any valid agent objects.")
    top_checks = [_duplicate_agent_id_check(configs)]
    command_base_dir = Path.cwd()
    agents = [_preflight_agent(config, resolver, codex_checker, command_base_dir=command_base_dir) for config in configs]
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
    codex_capability_checker: Callable[[list[str]], dict[str, str]],
    *,
    command_base_dir: Path,
) -> dict[str, object]:
    checks = [
        _agent_id_check(config.agent_id),
        _connection_kind_check(config.connection_kind),
    ]
    provider_connection_check = codex_provider_connection_check(config.provider_kind, config.connection_kind)
    if provider_connection_check is not None:
        checks.append(provider_connection_check)
    provider_connection_check = kiro_provider_connection_check(config.provider_kind, config.connection_kind)
    if provider_connection_check is not None:
        checks.append(provider_connection_check)
    provider_connection_check = cursor_provider_connection_check(config.provider_kind, config.connection_kind)
    if provider_connection_check is not None:
        checks.append(provider_connection_check)
    provider_connection_check = grok_provider_connection_check(config.provider_kind, config.connection_kind)
    if provider_connection_check is not None:
        checks.append(provider_connection_check)
    if config.connection_kind == "remote_bridge":
        checks.extend(
            [
                _remote_bridge_endpoint_check(config.endpoint),
                _remote_bridge_auth_ref_check(config.auth_ref),
            ]
        )
    else:
        command_check = _command_check(config.command, command_resolver)
        checks.append(command_check)
        if config.connection_kind == "self_service" and command_check["status"] == "ok":
            script_check = _python_script_check(config.command, command_base_dir=command_base_dir)
            if script_check is not None:
                checks.append(script_check)
        if config.connection_kind == "terminal_session":
            checks.append(_terminal_pty_check())
        if (
            config.provider_kind == "codex_live_session"
            and config.connection_kind == "live_session"
            and command_check["status"] == "ok"
        ):
            codex_command_check = _codex_command_check(config.command)
            checks.append(codex_command_check)
            if codex_command_check["status"] == "ok":
                checks.append(codex_capability_checker(_resolved_command(config.command, command_check.get("path", ""))))
        if (
            config.provider_kind == "kiro_live_session"
            and config.connection_kind == "live_session"
            and command_check["status"] == "ok"
        ):
            checks.append(kiro_command_check(config.command))
        if (
            config.provider_kind == "cursor_live_session"
            and config.connection_kind == "live_session"
            and command_check["status"] == "ok"
        ):
            checks.append(cursor_command_check(config.command))
        if (
            config.provider_kind == "grok_live_session"
            and config.connection_kind == "live_session"
            and command_check["status"] == "ok"
        ):
            checks.append(grok_command_check(config.command))
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
        "sandbox_enforcement": sandbox_launcher_for(config.provider_kind, config.connection_kind).enforcement,
        "command": config.command,
        "command_path": command_path,
        "status": status,
        "checks": checks,
    }


def resident_config_setup_error(
    config: ResidentAgentConfig,
    *,
    command_resolver: Callable[[str], str | None] | None = None,
    codex_capability_checker: Callable[[list[str]], dict[str, str]] | None = None,
    codex_command_runner: Callable[..., Any] | None = None,
) -> str:
    resolver = command_resolver or _resolve_command_path
    codex_checker = codex_capability_checker or (
        lambda command: _codex_exec_safety_flags_check(command, command_runner=codex_command_runner)
    )
    if config.connection_kind == "remote_bridge":
        return ""
    command_check = _command_check(config.command, resolver)
    if command_check["status"] != "ok":
        return str(command_check.get("message") or "Command is not executable.")
    if config.connection_kind == "self_service":
        script_check = _python_script_check(config.command, command_base_dir=Path.cwd())
        if script_check is not None and script_check["status"] != "ok":
            return str(script_check.get("message") or "Command script is not available.")
    if config.provider_kind == "codex_live_session" and config.connection_kind == "live_session":
        codex_command_check = _codex_command_check(config.command)
        if codex_command_check["status"] != "ok":
            return str(codex_command_check.get("message") or "Codex command is not valid.")
        capability_check = codex_checker(_resolved_command(config.command, command_check.get("path", "")))
        if capability_check["status"] != "ok":
            return str(capability_check.get("message") or "Codex command is not ready.")
    if config.provider_kind == "kiro_live_session" and config.connection_kind == "live_session":
        kiro_check = kiro_command_check(config.command)
        if kiro_check["status"] != "ok":
            return str(kiro_check.get("message") or "Kiro command is not valid.")
    if config.provider_kind == "cursor_live_session" and config.connection_kind == "live_session":
        cursor_check = cursor_command_check(config.command)
        if cursor_check["status"] != "ok":
            return str(cursor_check.get("message") or "Cursor command is not valid.")
    if config.provider_kind == "grok_live_session" and config.connection_kind == "live_session":
        grok_check = grok_command_check(config.command)
        if grok_check["status"] != "ok":
            return str(grok_check.get("message") or "Grok command is not valid.")
    if config.connection_kind == "terminal_session" and not terminal_sessions_supported():
        return "PTY terminal sessions are not available on this host."
    return ""


def _load_preflight_configs(path: Path, *, server_override: str | None = None) -> list[ResidentAgentConfig]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Live agent group config must be a JSON object.")
    server = str(server_override or data.get("server") or "http://127.0.0.1:8765")
    defaults = {
        "poll_interval": live_agent_nonnegative_float(data.get("poll_interval"), 2.0, "poll_interval"),
        "heartbeat_interval": live_agent_nonnegative_float(data.get("heartbeat_interval"), 30.0, "heartbeat_interval"),
        "cooldown": live_agent_nonnegative_float(data.get("cooldown"), 5.0, "cooldown"),
        "max_chain_depth": live_agent_nonnegative_int(data.get("max_chain_depth"), 1, "max_chain_depth"),
        "max_ticks": live_agent_nonnegative_int(data.get("max_ticks"), 0, "max_ticks"),
    }
    agents = data.get("agents")
    if not isinstance(agents, list) or not agents:
        raise ValueError("Live agent group config requires a non-empty agents list.")
    if not all(isinstance(agent, dict) for agent in agents):
        raise ValueError("Each live agent entry must be a JSON object.")
    return [
        _preflight_config_from_mapping(agent, server=server, defaults=defaults, server_override=server_override)
        for agent in agents
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
    provider_kind = str(data.get("provider_kind") or "local_cli")
    connection_kind = str(data.get("connection_kind") or "local_cli")
    command_parts = live_agent_command_parts(command)
    command_parts = default_codex_resident_command(provider_kind, connection_kind, command_parts)
    command_parts = default_cursor_resident_command(provider_kind, connection_kind, command_parts)
    command_parts = default_kiro_resident_command(provider_kind, connection_kind, command_parts)
    command_parts = default_grok_resident_command(provider_kind, connection_kind, command_parts)
    return ResidentAgentConfig(
        server=str(server_override or data.get("server") or server),
        agent_id=str(data.get("agent_id") or ""),
        display_name=str(data.get("display_name") or data.get("agent_id") or ""),
        provider_kind=provider_kind,
        connection_kind=connection_kind,
        session_id=str(data.get("session_id") or ""),
        endpoint=endpoint if isinstance(endpoint, str) else "",
        auth_ref=auth_ref if isinstance(auth_ref, str) else "",
        meeting_id=str(data.get("meeting_id") or ""),
        engagement_mode=str(data.get("engagement_mode") or "mentioned"),
        command=command_parts,
        timeout_seconds=int(data.get("timeout_seconds") or data.get("timeout") or 120),
        poll_interval=live_agent_nonnegative_float(data.get("poll_interval"), defaults["poll_interval"], "poll_interval"),
        heartbeat_interval=live_agent_nonnegative_float(
            data.get("heartbeat_interval"),
            defaults["heartbeat_interval"],
            "heartbeat_interval",
        ),
        cooldown=live_agent_nonnegative_float(data.get("cooldown"), defaults["cooldown"], "cooldown"),
        max_chain_depth=live_agent_nonnegative_int(
            data.get("max_chain_depth"),
            defaults["max_chain_depth"],
            "max_chain_depth",
        ),
        max_ticks=live_agent_nonnegative_int(data.get("max_ticks"), defaults["max_ticks"], "max_ticks"),
        terminal_idle_timeout=live_agent_nonnegative_float(
            data.get("terminal_idle_timeout"),
            0.35,
            "terminal_idle_timeout",
        ),
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


def _python_script_check(command: list[str], *, command_base_dir: Path) -> dict[str, str] | None:
    script_arg = _python_script_argument(command)
    if script_arg is None:
        return None
    script_path = Path(script_arg)
    resolved = script_path if script_path.is_absolute() else (command_base_dir / script_path).resolve()
    if resolved.is_file():
        return {
            "id": "command_script",
            "status": "ok",
            "message": f"Command script found: {script_arg}",
            "path": str(resolved),
        }
    return {
        "id": "command_script",
        "status": "failed",
        "message": f"Command script not found: {script_arg}",
    }


def _python_script_argument(command: list[str]) -> str | None:
    if not command:
        return None
    if not _is_python_executable_name(Path(command[0]).name):
        return None
    index = 1
    while index < len(command):
        part = command[index]
        if _is_python_inline_or_module_option(part):
            return None
        if part.startswith("-"):
            index += 1
            continue
        if part.endswith(".py"):
            return part
        return None
    return None


def _is_python_inline_or_module_option(part: str) -> bool:
    return part in {"-c", "-m"} or part.startswith("-c") or part.startswith("-m")


def _is_python_executable_name(name: str) -> bool:
    return bool(re.fullmatch(r"python(?:\d+(?:\.\d+)?)?(?:\.exe)?", name))


def _terminal_pty_check() -> dict[str, str]:
    if terminal_sessions_supported():
        return {"id": "terminal_pty", "status": "ok", "message": "PTY terminal sessions are available."}
    return {
        "id": "terminal_pty",
        "status": "failed",
        "message": "PTY terminal sessions are not available on this host.",
    }


def _codex_command_check(command: list[str]) -> dict[str, str]:
    executable = str(command[0] if command else "").strip()
    if len(command) != 1:
        return {
            "id": "codex_command",
            "status": "failed",
            "message": "codex_live_session command must contain only the codex executable.",
        }
    if Path(executable).name in {"codex", "codex.exe"}:
        return {
            "id": "codex_command",
            "status": "ok",
            "message": "codex_live_session command executable is codex.",
        }
    return {
        "id": "codex_command",
        "status": "failed",
        "message": "codex_live_session command executable must be named codex.",
    }


def _resolved_command(command: list[str], resolved_path: str) -> list[str]:
    if not command:
        return command
    return [resolved_path or command[0], *command[1:]]


def _codex_exec_safety_flags_check(
    command: list[str],
    *,
    command_runner: Callable[..., Any] | None = None,
) -> dict[str, str]:
    if not command:
        return {"id": "codex_exec_safety_flags", "status": "failed", "message": "Codex command is empty."}
    probe_command = [
        *codex_exec_prefix(command),
        "resume",
        "--skip-git-repo-check",
        "--help",
    ]
    runner = command_runner or subprocess.run
    try:
        completed = runner(
            probe_command,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except TimeoutExpired:
        return {
            "id": "codex_exec_safety_flags",
            "status": "failed",
            "message": "Codex safety flag probe timed out.",
        }
    except OSError as error:
        return {
            "id": "codex_exec_safety_flags",
            "status": "failed",
            "message": f"Codex safety flag probe could not run: {error.__class__.__name__}.",
        }
    if int(getattr(completed, "returncode", 1) or 0) == 0:
        return {
            "id": "codex_exec_safety_flags",
            "status": "ok",
            "message": "Codex exec read-only safety flags are available.",
        }
    returncode = int(getattr(completed, "returncode", 1) or 0)
    display_probe = shlex.join([Path(probe_command[0]).name, *probe_command[1:]])
    return {
        "id": "codex_exec_safety_flags",
        "status": "failed",
        "message": (
            f"Codex command rejected the required live-session safety flags with exit {returncode}. "
            f"Run `{display_probe}` to inspect the local Codex CLI."
        ),
    }


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
