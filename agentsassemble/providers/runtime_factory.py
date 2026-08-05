from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from agentsassemble.providers.antigravity_hooks import AntigravityHookRuntime
from agentsassemble.providers.api_context import DEFAULT_API_CONTEXT_CONTRACT_BYTES
from agentsassemble.providers.claude_hooks import ClaudeHookRuntime
from agentsassemble.providers.codex_app_server_live import CodexAppServerLiveRuntime
from agentsassemble.providers.cursor_room_portal import CursorRoomPortalRuntime
from agentsassemble.providers.grok_acp import GrokAcpRuntime
from agentsassemble.providers.live_cli import LiveCliRuntime
from agentsassemble.providers.local_openai import LocalOpenAICompatibleRuntime
from agentsassemble.providers.native_harness import native_harness_runtime
from agentsassemble.providers.opencode import OpenCodeRuntime
from agentsassemble.providers.remote_openai import (
    RemoteOpenAICompatibleRuntime,
    remote_openai_profile,
)
from agentsassemble.providers.runtime_config import ProviderRuntimeConfig
from agentsassemble.providers.terminal_interactions import AntigravityRoomPortalInteraction
from agentsassemble.providers.windows_conpty import WindowsConPtyRuntime

if TYPE_CHECKING:
    from agentsassemble.providers.room_portal import RoomPortal


class ProviderRuntimeFactoryError(ValueError):
    def __init__(self, message: str, *, code: str = "unsupported_provider_transport") -> None:
        super().__init__(message)
        self.code = code


_TERMINAL_RUNTIME_KINDS = {
    ("codex_live_session", "pty"): "live_cli",
    ("antigravity_live_session", "pty"): "live_cli",
    ("claude_code", "pty"): "live_cli",
    ("cursor_live_session", "pty"): "live_cli",
    ("local_cli", "pty"): "live_cli",
    ("codex_live_session", "conpty"): "live_cli",
    ("antigravity_live_session", "conpty"): "live_cli",
    ("claude_code", "conpty"): "live_cli",
    ("cursor_live_session", "conpty"): "live_cli",
    ("local_cli", "conpty"): "live_cli",
}

_STRUCTURED_RUNTIME_KINDS = {
    ("grok_live_session", "acp_stdio"): "live_cli",
    ("opencode_server", "http"): "opencode",
    ("cerebras_api", "https"): "api",
    ("deepseek_api", "https"): "api",
    ("openrouter_api", "https"): "api",
    ("vercel_ai_gateway", "https"): "api",
    ("llm_gateway_api", "https"): "api",
    ("tokenrouter_api", "https"): "api",
    ("custom_openai_api", "https"): "api",
    ("ollama_api", "http"): "api",
    ("lmstudio_api", "http"): "api",
}


def runtime_from_config(
    config: ProviderRuntimeConfig,
    *,
    credential: str = "",
    environment: dict[str, str] | None = None,
    room_portal: RoomPortal | None = None,
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
    if config.execution_harness != "builtin":
        if config.runtime_kind != "api":
            raise ProviderRuntimeFactoryError(
                "Alternate execution harnesses require an API or Local provider.",
                code="provider_runtime_kind_mismatch",
            )
        remote_profile = remote_openai_profile(config.provider_kind)
        return native_harness_runtime(
            agent_id=config.participant_id,
            harness=config.execution_harness,
            runtime_kind=config.runtime_kind,
            provider_kind=config.provider_kind,
            provider_endpoint=config.provider_endpoint,
            credential=credential,
            model=config.model,
            reasoning_effort=config.reasoning_effort,
            permission_mode=config.permission_mode,
            service_tier=config.service_tier,
            workspace=config.cwd,
            runtime_state_dir=config.runtime_state_dir,
            environment=environment,
            room_portal=room_portal,
            request_headers=(
                remote_profile.request_headers if remote_profile is not None else ()
            ),
            variant=config.variant,
            max_output_tokens=config.max_output_tokens,
            context_contract_bytes=(
                config.context_contract_bytes
                or DEFAULT_API_CONTEXT_CONTRACT_BYTES
            ),
        )
    if key == ("codex_live_session", "pty"):
        return CodexAppServerLiveRuntime(
            config.participant_id,
            workspace=config.cwd,
            model=config.model,
            reasoning_effort=config.reasoning_effort,
            permission_mode=config.permission_mode,
            service_tier=config.service_tier,
            executable=config.command[0],
            environment=environment,
            room_portal=room_portal,
        )
    remote_profile = remote_openai_profile(config.provider_kind)
    if remote_profile is not None and config.transport == "https":
        return RemoteOpenAICompatibleRuntime(
            config.participant_id,
            profile=remote_profile,
            api_key=credential,
            model=config.model,
            reasoning_effort=config.reasoning_effort,
            variant=config.variant,
            max_output_tokens=config.max_output_tokens,
            base_url=config.provider_endpoint,
            room_portal=room_portal,
            workspace=config.cwd,
            permission_mode=config.permission_mode,
            state_dir=config.runtime_state_dir,
            context_contract_bytes=config.context_contract_bytes,
            resume_required=config.resume_required,
        )
    if key == ("ollama_api", "http"):
        return LocalOpenAICompatibleRuntime(
            config.participant_id,
            provider_name="Ollama",
            model=config.model,
            base_url=config.provider_endpoint,
            message_source="ollama_sse",
            room_portal=room_portal,
            workspace=config.cwd,
            permission_mode=config.permission_mode,
            state_dir=config.runtime_state_dir,
            resume_required=config.resume_required,
        )
    if key == ("lmstudio_api", "http"):
        return LocalOpenAICompatibleRuntime(
            config.participant_id,
            provider_name="LM Studio",
            model=config.model,
            base_url=config.provider_endpoint,
            message_source="lmstudio_sse",
            room_portal=room_portal,
            workspace=config.cwd,
            permission_mode=config.permission_mode,
            state_dir=config.runtime_state_dir,
            resume_required=config.resume_required,
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
            room_portal=room_portal,
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
            env=environment,
            room_portal=room_portal,
            startup_timeout_seconds=config.startup_timeout_seconds,
        )
    runtime_class = WindowsConPtyRuntime if os.name == "nt" else LiveCliRuntime
    if key in {
        ("cursor_live_session", "pty"),
        ("cursor_live_session", "conpty"),
    } and room_portal is not None:
        return CursorRoomPortalRuntime(
            config,
            room_portal=room_portal,
            runtime_factory=runtime_class,
            environment=environment,
        )
    antigravity_runtime = key in {
        ("antigravity_live_session", "pty"),
        ("antigravity_live_session", "conpty"),
    }
    claude_runtime = key in {
        ("claude_code", "pty"),
        ("claude_code", "conpty"),
    }
    runtime_kwargs = {
        "env": environment,
        "idle_quiet_seconds": config.quiet_seconds,
        "input_mode": config.input_mode,
        "submit_newline": config.submit_newline,
        "submit_delay_seconds": config.submit_delay_seconds,
        "terminal_rows": config.terminal_rows,
        "terminal_columns": config.terminal_columns,
        "startup_quiet_seconds": config.startup_quiet_seconds,
        "startup_timeout_seconds": config.startup_timeout_seconds,
        "startup_accept_contains": config.startup_accept_contains,
        "startup_accept_keys": config.startup_accept_keys,
        "startup_ready_contains": config.startup_ready_contains,
        "startup_input": config.startup_input,
        "terminal_interaction_policy": (
            AntigravityRoomPortalInteraction(defer_external_permissions=True)
            if antigravity_runtime
            else None
        ),
        "profile_settings": {
            "model": config.model,
            "reasoning_effort": config.reasoning_effort,
            "service_tier": config.service_tier,
            "variant": config.variant,
            "permission_mode": config.permission_mode,
        },
    }
    if antigravity_runtime:
        return AntigravityHookRuntime(
            config.participant_id,
            list(config.command),
            cwd=config.cwd,
            terminal_runtime_factory=runtime_class,
            **runtime_kwargs,
        )
    if claude_runtime:
        return ClaudeHookRuntime(
            config.participant_id,
            list(config.command),
            cwd=config.cwd,
            state_dir=config.runtime_state_dir,
            terminal_runtime_factory=runtime_class,
            **runtime_kwargs,
        )
    return runtime_class(
        config.participant_id,
        list(config.command),
        cwd=config.cwd,
        **runtime_kwargs,
    )


def _is_grok_acp_command(command: list[str]) -> bool:
    executable = Path(command[0]).name.casefold() if command else ""
    parts = [str(part).casefold() for part in command[1:]]
    return executable == "grok" and "agent" in parts and "stdio" in parts
