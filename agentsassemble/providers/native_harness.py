"""Native coding-harness adapters for API and Local model providers.

The room still owns session lifecycle, publication, and approvals.  This module
only changes the model wire beneath an existing Codex or Claude Code harness.
"""

from __future__ import annotations

import shutil

from agentsassemble.providers.codex_app_server_live import CodexAppServerLiveRuntime
from agentsassemble.providers.live_cli import LiveCliRuntime
from agentsassemble.providers.native_harness_gateway import NativeModelGateway
from agentsassemble.providers.room_portal import RoomPortal
from agentsassemble.room.text import clean_room_text


class NativeHarnessUnavailable(RuntimeError):
    pass


class NativeHarnessRuntime:
    """Keep gateway and native harness lifetime aligned with one room runtime."""

    def __init__(self, delegate, *, harness: str, gateway: NativeModelGateway | None) -> None:
        self.delegate = delegate
        self.harness = harness
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
        self.delegate.send_room_observation(text, media_blocks=media_blocks)

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
            "execution_harness": self.harness,
            "harness_gateway": "internal" if self.gateway else "direct",
            "harness_gateway_pid": self.gateway.pid if self.gateway else None,
            "harness_gateway_request_count": gateway_health.get("request_count", 0),
            "harness_gateway_last_request": gateway_health.get("last_request_kind", ""),
            "harness_gateway_last_error": gateway_health.get("last_error", ""),
        }


def native_harness_runtime(
    *,
    agent_id: str,
    harness: str,
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
):
    del runtime_state_dir
    selected = clean_room_text(harness, limit=32)
    if selected not in {"codex", "claude"}:
        raise NativeHarnessUnavailable(f"Unsupported native harness: {selected}")
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
        delegate = CodexAppServerLiveRuntime(
            agent_id,
            workspace=workspace,
            model=model,
            reasoning_effort=reasoning_effort,
            permission_mode=permission_mode,
            service_tier=service_tier,
            executable=executable,
            environment=environment,
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
        command = [executable, "--model", model]
        if reasoning_effort:
            command.extend(("--effort", reasoning_effort))
        if permission_mode == "workspace_write":
            command.extend(("--permission-mode", "auto"))
        else:
            command.extend(
                (
                    "--permission-mode",
                    "dontAsk",
                    "--tools",
                    "Bash",
                    "--allowedTools",
                    "Bash(agentsassemble-room *)",
                )
            )
        command.append("--safe-mode")
        delegate = LiveCliRuntime(
            agent_id,
            command,
            cwd=workspace,
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
    return NativeHarnessRuntime(delegate, harness=selected, gateway=gateway)


def _supports_direct_harness(provider_kind: str, harness: str) -> bool:
    return (
        harness == "codex" and provider_kind in {"ollama_api", "lmstudio_api"}
    ) or (
        harness == "claude" and provider_kind == "lmstudio_api"
    )


__all__ = [
    "NativeHarnessRuntime",
    "NativeHarnessUnavailable",
    "native_harness_runtime",
]
