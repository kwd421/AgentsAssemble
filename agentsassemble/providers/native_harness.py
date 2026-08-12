"""Native coding-harness adapters for API and Local model providers.

The room still owns session lifecycle, publication, and approvals. This module
keeps gateway lifetime aligned with Codex/Claude harnesses and dispatches other
registered harnesses through ``harness_registry``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from agentsassemble.providers.claude_command import claude_interactive_command
from agentsassemble.providers.claude_hooks import ClaudeHookRuntime
from agentsassemble.providers.codex_app_server_live import CodexAppServerLiveRuntime
from agentsassemble.providers.live_cli import LiveCliRuntime
from agentsassemble.providers.native_harness_gateway import NativeModelGateway
from agentsassemble.providers.room_portal import RoomPortal
from agentsassemble.room.text import clean_room_text


class NativeHarnessUnavailable(RuntimeError):
    pass


class NativeHarnessRuntime:
    """Keep gateway and native harness lifetime aligned with one room runtime."""

    def __init__(
        self,
        delegate,
        *,
        harness: str,
        runtime_kind: str,
        gateway: NativeModelGateway | None,
    ) -> None:
        self.delegate = delegate
        self.harness = harness
        self.runtime_kind = runtime_kind
        self.gateway = gateway

    def set_request_handler(self, handler) -> None:
        setter = getattr(self.delegate, "set_request_handler", None)
        if callable(setter):
            setter(handler)

    def start(self) -> dict[str, object]:
        if self.gateway is not None:
            self.gateway.start()
        try:
            return self.health(start_delegate=True)
        except Exception:
            if self.gateway is not None:
                self.gateway.stop()
            raise

    def send(self, text: str) -> None:
        self.start()
        self.delegate.send(text)

    def send_room_observation(self, text: str, *, media_blocks=None) -> None:
        self.start()
        sender = getattr(self.delegate, "send_room_observation", None)
        if callable(sender):
            sender(text, media_blocks=media_blocks)
            return
        self.delegate.send(text)

    def read_output(self, *, timeout_seconds: float, on_delta=None, on_activity=None):
        return self.delegate.read_output(
            timeout_seconds=timeout_seconds,
            on_delta=on_delta,
            on_activity=on_activity,
        )

    def interrupt(self) -> None:
        self.delegate.interrupt()

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        try:
            self.delegate.stop(timeout_seconds=timeout_seconds)
        finally:
            if self.gateway is not None:
                self.gateway.stop(timeout_seconds=timeout_seconds)

    def health(self, *, start_delegate: bool = False) -> dict[str, object]:
        details = (
            self.delegate.start()
            if start_delegate
            else self.delegate.health()
        )
        gateway_health = self.gateway.health() if self.gateway else {}
        return {
            **dict(details),
            # The wrapper owns the provider profile identity. The delegate's
            # live_cli kind describes its transport implementation, not the
            # API runtime exposed to the canonical bridge.
            "runtime_kind": self.runtime_kind,
            "execution_harness": self.harness,
            "harness_gateway": "internal" if self.gateway else "direct",
            "harness_gateway_pid": self.gateway.pid if self.gateway else None,
            "harness_gateway_request_count": gateway_health.get("request_count", 0),
            "harness_gateway_last_request": gateway_health.get("last_request_kind", ""),
            "harness_gateway_last_error": gateway_health.get("last_error", ""),
            "harness_gateway_compacted_tool_result_count": gateway_health.get(
                "compacted_tool_result_count", 0
            ),
            "harness_gateway_last_request_context_bytes": gateway_health.get(
                "last_request_context_bytes", 0
            ),
        }


def native_harness_runtime(
    *,
    agent_id: str,
    harness: str,
    runtime_kind: str,
    provider_kind: str,
    provider_endpoint: str,
    credential: str,
    model: str,
    reasoning_effort: str,
    permission_mode: str,
    service_tier: str,
    workspace: str,
    runtime_state_dir: str,
    environment: dict[str, str] | None,
    room_portal: RoomPortal | None,
    request_headers: tuple[tuple[str, str], ...] = (),
    variant: str = "",
    max_output_tokens: int = 0,
    context_contract_bytes: int = 256_000,
):
    """Create Codex/Claude harnesses. Other harnesses go through harness_registry."""

    return create_codex_or_claude_harness(
        agent_id=agent_id,
        harness=harness,
        runtime_kind=runtime_kind,
        provider_kind=provider_kind,
        provider_endpoint=provider_endpoint,
        credential=credential,
        model=model,
        reasoning_effort=reasoning_effort,
        permission_mode=permission_mode,
        service_tier=service_tier,
        workspace=workspace,
        runtime_state_dir=runtime_state_dir,
        environment=environment,
        room_portal=room_portal,
        request_headers=request_headers,
        variant=variant,
        max_output_tokens=max_output_tokens,
        context_contract_bytes=context_contract_bytes,
    )


def create_codex_or_claude_harness(
    *,
    agent_id: str,
    harness: str,
    runtime_kind: str,
    provider_kind: str,
    provider_endpoint: str,
    credential: str,
    model: str,
    reasoning_effort: str,
    permission_mode: str,
    service_tier: str,
    workspace: str,
    runtime_state_dir: str,
    environment: dict[str, str] | None,
    room_portal: RoomPortal | None,
    request_headers: tuple[tuple[str, str], ...] = (),
    variant: str = "",
    max_output_tokens: int = 0,
    context_contract_bytes: int = 256_000,
) -> NativeHarnessRuntime:
    selected = clean_room_text(harness, limit=32).casefold()
    if selected not in {"codex", "claude"}:
        raise NativeHarnessUnavailable(f"Unsupported Codex/Claude harness: {selected}")
    gateway: NativeModelGateway | None = None
    harness_endpoint = provider_endpoint
    if not _supports_direct_harness(provider_kind, selected):
        gateway = NativeModelGateway(
            upstream_base_url=provider_endpoint,
            upstream_api_key=credential,
            model=model,
            provider_kind=provider_kind,
            reasoning_effort=reasoning_effort,
            variant=variant,
            max_output_tokens=max_output_tokens,
            request_headers=request_headers,
            context_contract_bytes=context_contract_bytes,
            state_dir=runtime_state_dir,
        )
        harness_endpoint = gateway.endpoint
    if selected == "codex":
        executable = shutil.which("codex")
        if not executable:
            raise NativeHarnessUnavailable("Codex CLI is not installed.")
        model_provider = {
            "ollama_api": "ollama",
            "lmstudio_api": "lmstudio",
        }.get(provider_kind, "agentsassemble_harness")
        state_root = clean_room_text(runtime_state_dir, limit=1000)
        if not state_root:
            raise NativeHarnessUnavailable(
                "Codex harness requires an isolated runtime state directory."
            )
        codex_home = Path(state_root).expanduser().resolve() / "codex-home"
        codex_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        codex_environment = dict(environment or {})
        codex_environment["CODEX_HOME"] = str(codex_home)
        delegate = CodexAppServerLiveRuntime(
            agent_id,
            workspace=workspace,
            model=model,
            reasoning_effort=reasoning_effort,
            permission_mode=permission_mode,
            service_tier=service_tier,
            executable=executable,
            environment=codex_environment,
            room_portal=room_portal,
            model_provider=model_provider,
            provider_base_url=(
                harness_endpoint if model_provider == "agentsassemble_harness" else ""
            ),
        )
    else:
        executable = shutil.which("claude")
        if not executable:
            raise NativeHarnessUnavailable("Claude Code CLI is not installed.")
        base_url = harness_endpoint.removesuffix("/v1")
        claude_env = dict(environment or {})
        claude_env.update(
            {
                "ANTHROPIC_BASE_URL": base_url,
                "ANTHROPIC_AUTH_TOKEN": "agentsassemble-local-gateway",
                "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST": "1",
                "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
            }
        )
        command = claude_interactive_command(
            executable=executable,
            model=model,
            reasoning_effort=reasoning_effort,
            permission_mode=permission_mode,
            workspace_write_mode="auto",
        )
        delegate = ClaudeHookRuntime(
            agent_id,
            command,
            cwd=workspace,
            state_dir=runtime_state_dir,
            terminal_runtime_factory=LiveCliRuntime,
            env=claude_env,
            input_mode="bracketed_paste",
            submit_newline="\r",
            startup_quiet_seconds=1.0,
            startup_timeout_seconds=20.0,
            startup_accept_contains="Quick safety check",
            startup_ready_contains="for agents",
            profile_settings={
                "model": model,
                "reasoning_effort": reasoning_effort,
                "service_tier": service_tier,
                "permission_mode": permission_mode,
            },
        )
    return NativeHarnessRuntime(
        delegate,
        harness=selected,
        runtime_kind=runtime_kind,
        gateway=gateway,
    )


def _supports_direct_harness(provider_kind: str, harness: str) -> bool:
    return (
        harness == "codex" and provider_kind in {"ollama_api", "lmstudio_api"}
    ) or (
        harness == "claude" and provider_kind == "lmstudio_api"
    )


__all__ = [
    "NativeHarnessRuntime",
    "NativeHarnessUnavailable",
    "create_codex_or_claude_harness",
    "native_harness_runtime",
]
