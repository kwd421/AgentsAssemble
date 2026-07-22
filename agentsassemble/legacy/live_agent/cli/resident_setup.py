"""Preflight and worker-error handling for retained resident commands."""
from __future__ import annotations

import re
import subprocess
import urllib.parse
from collections.abc import Callable
from typing import Any

from agentsassemble.legacy.live_agent.state import _looks_sensitive_presence_error
from agentsassemble.live_agent_runner import ResidentAgentConfig


def should_heartbeat_resident_worker_error(
    config: ResidentAgentConfig,
    error: BaseException,
) -> bool:
    return not (
        config.connection_kind == "self_service"
        and isinstance(error, subprocess.CalledProcessError)
    )


def heartbeat_resident_worker_error(
    config: ResidentAgentConfig,
    error: BaseException,
    *,
    request_json: Callable[..., dict[str, object]],
    server_url: Callable[[str, str], str],
) -> None:
    try:
        request_json(
            server_url(
                config.server,
                f"/api/live-agents/{urllib.parse.quote(config.agent_id, safe='')}/heartbeat",
            ),
            method="POST",
            payload={"status": "error", "last_error": resident_worker_error_message(error)},
            timeout_seconds=2.0,
        )
    except Exception:
        return


def resident_worker_error_message(error: BaseException) -> str:
    message = str(error).strip()
    if message and _looks_sensitive_presence_error(message):
        return "Resident worker error details redacted."
    error_type = type(error).__name__
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", error_type):
        return f"Resident worker failed with {error_type}."
    return "Resident worker failed."


def resident_group_config_errors(
    configs: list[ResidentAgentConfig],
    *,
    setup_error: Callable[[ResidentAgentConfig], str],
) -> dict[str, str]:
    errors = duplicate_resident_agent_id_errors(configs)
    for config in configs:
        if config.agent_id in errors:
            continue
        try:
            error = setup_error(config)
            if error:
                errors[config.agent_id] = error
        except Exception as caught:
            errors[config.agent_id] = str(caught)
    return errors


def duplicate_resident_agent_id_errors(
    configs: list[ResidentAgentConfig],
) -> dict[str, str]:
    counts: dict[str, int] = {}
    for config in configs:
        if config.agent_id:
            counts[config.agent_id] = counts.get(config.agent_id, 0) + 1
    return {
        agent_id: "Duplicate agent id in resident group config."
        for agent_id, count in counts.items()
        if count > 1
    }


def resident_config_setup_error(
    config: ResidentAgentConfig,
    *,
    validate_config: Callable[[ResidentAgentConfig], None],
    command_runner_for_config: Callable[[ResidentAgentConfig], Any],
    close_command_runner: Callable[[Any], None],
    provider_setup_error: Callable[[ResidentAgentConfig], str],
) -> str:
    validate_config(config)
    if config.connection_kind == "remote_bridge":
        probe_runner = command_runner_for_config(config)
        close_command_runner(probe_runner)
        return ""
    return provider_setup_error(config)
