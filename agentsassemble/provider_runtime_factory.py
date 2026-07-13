from __future__ import annotations

import os
from pathlib import Path

from agentsassemble.deepseek_runtime import DeepSeekApiRuntime
from agentsassemble.grok_acp_runtime import GrokAcpRuntime
from agentsassemble.live_cli import LiveCliRuntime
from agentsassemble.opencode_runtime import OpenCodeRuntime
from agentsassemble.provider_runtime_config import ProviderRuntimeConfig
from agentsassemble.windows_conpty import WindowsConPtyRuntime


class ProviderRuntimeFactoryError(ValueError):
    def __init__(self, message: str, *, code: str = "unsupported_provider_transport") -> None:
        super().__init__(message)
        self.code = code


_TERMINAL_RUNTIME_KINDS = {
    ("codex_live_session", "pty"): "live_cli",
    ("antigravity_live_session", "pty"): "live_cli",
    ("claude_code", "pty"): "live_cli",
    ("local_cli", "pty"): "live_cli",
    ("codex_live_session", "conpty"): "live_cli",
    ("antigravity_live_session", "conpty"): "live_cli",
    ("claude_code", "conpty"): "live_cli",
    ("local_cli", "conpty"): "live_cli",
}

_STRUCTURED_RUNTIME_KINDS = {
    ("grok_live_session", "acp_stdio"): "live_cli",
    ("opencode_server", "http"): "opencode",
    ("deepseek_api", "https"): "api",
}


def runtime_from_config(
    config: ProviderRuntimeConfig,
    *,
    credential: str = "",
):
    key = (config.provider_kind, config.transport)
    expected_runtime_kind = _TERMINAL_RUNTIME_KINDS.get(key) or _STRUCTURED_RUNTIME_KINDS.get(key)
    if expected_runtime_kind is None:
        raise ProviderRuntimeFactoryError(
            f"Unsupported provider runtime pair: {config.provider_kind}/{config.transport}."
        )
    if config.runtime_kind != expected_runtime_kind:
        raise ProviderRuntimeFactoryError(
            "Provider runtime kind does not match its provider and transport.",
            code="provider_runtime_kind_mismatch",
        )
    if key == ("deepseek_api", "https"):
        return DeepSeekApiRuntime(
            config.participant_id,
            api_key=credential,
            model=config.model,
            reasoning_effort=config.reasoning_effort,
            thinking=config.variant != "non_thinking",
        )
    if key == ("opencode_server", "http"):
        return OpenCodeRuntime(
            config.participant_id,
            endpoint=config.provider_endpoint,
            workspace=config.cwd,
            state_dir=config.runtime_state_dir,
            model=config.model,
            variant=config.variant,
            permission_mode=config.permission_mode,
            server_pid=config.provider_server_pid,
        )
    if key == ("grok_live_session", "acp_stdio"):
        command = list(config.command)
        if not _is_grok_acp_command(command):
            raise ProviderRuntimeFactoryError(
                "Grok Agent Sessions require the exact grok agent stdio transport.",
                code="provider_command_transport_mismatch",
            )
        return GrokAcpRuntime(
            config.participant_id,
            command,
            cwd=config.cwd,
            state_dir=config.runtime_state_dir,
            startup_timeout_seconds=config.startup_timeout_seconds,
        )
    runtime_class = WindowsConPtyRuntime if os.name == "nt" else LiveCliRuntime
    return runtime_class(
        config.participant_id,
        list(config.command),
        cwd=config.cwd,
        idle_quiet_seconds=config.quiet_seconds,
        input_mode=config.input_mode,
        submit_newline=config.submit_newline,
        submit_delay_seconds=config.submit_delay_seconds,
        terminal_rows=config.terminal_rows,
        terminal_columns=config.terminal_columns,
        startup_quiet_seconds=config.startup_quiet_seconds,
        startup_timeout_seconds=config.startup_timeout_seconds,
        startup_accept_contains=config.startup_accept_contains,
        startup_accept_keys=config.startup_accept_keys,
        startup_ready_contains=config.startup_ready_contains,
        startup_input=config.startup_input,
        profile_settings={
            "model": config.model,
            "reasoning_effort": config.reasoning_effort,
            "service_tier": config.service_tier,
            "variant": config.variant,
            "permission_mode": config.permission_mode,
        },
    )


def _is_grok_acp_command(command: list[str]) -> bool:
    executable = Path(command[0]).name.casefold() if command else ""
    parts = [str(part).casefold() for part in command[1:]]
    return executable == "grok" and "agent" in parts and "stdio" in parts
