"""Native coding-harness adapters for API and Local model providers.

The room still owns session lifecycle, publication, and approvals.  This module
only changes the model wire beneath an existing Codex or Claude Code harness.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

from agentsassemble.providers.codex_app_server_live import CodexAppServerLiveRuntime
from agentsassemble.providers.live_cli import LiveCliRuntime
from agentsassemble.providers.process_environment import sanitized_child_environment
from agentsassemble.providers.room_portal import RoomPortal
from agentsassemble.room.text import clean_room_text


HARNESS_GATEWAY_ENV_KEY = "AGENTSASSEMBLE_HARNESS_UPSTREAM_KEY"


class NativeHarnessUnavailable(RuntimeError):
    pass


class OpenCodexHarnessGateway:
    """Own one loopback-only OpenCodex translator for one Agent Session."""

    def __init__(
        self,
        *,
        state_dir: str | Path,
        upstream_base_url: str,
        upstream_api_key: str,
        model: str,
        request_headers: tuple[tuple[str, str], ...] = (),
        executable: str = "",
    ) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.upstream_base_url = str(upstream_base_url or "").rstrip("/")
        self.upstream_api_key = str(upstream_api_key or "")
        self.model = clean_room_text(model, limit=256)
        self.request_headers = tuple(
            (str(name), str(value))
            for name, value in request_headers
            if str(name).strip() and str(value).strip()
        )
        self.executable = executable or _resolve_opencodex_executable()
        self.port = _reserve_loopback_port()
        self.endpoint = f"http://127.0.0.1:{self.port}/v1"
        self.process: subprocess.Popen[bytes] | None = None
        self._stderr_file = None

    def start(self, *, timeout_seconds: float = 12.0) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        if not self.executable:
            raise NativeHarnessUnavailable(
                "OpenCodex gateway is required for this API harness. "
                "Install @bitkyc08/opencodex and refresh the provider catalog."
            )
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "codex-home").mkdir(parents=True, exist_ok=True)
        config_path = self.state_dir / "config.json"
        stderr_path = self.state_dir / "gateway.stderr.log"
        provider: dict[str, object] = {
            "adapter": "openai-chat",
            "baseUrl": self.upstream_base_url,
            "defaultModel": self.model,
            "models": [self.model],
            "liveModels": False,
        }
        if self.upstream_api_key:
            provider["apiKey"] = f"${{{HARNESS_GATEWAY_ENV_KEY}}}"
        if _is_loopback_url(self.upstream_base_url):
            provider["allowPrivateNetwork"] = True
        if self.request_headers:
            provider["headers"] = dict(self.request_headers)
        config = {
            "port": self.port,
            "hostname": "127.0.0.1",
            "providers": {"agentsassemble": provider},
            "defaultProvider": "agentsassemble",
            "multiAgentGuidanceEnabled": False,
            "websockets": False,
            "codexAutoStart": False,
            "codexShimAutoRestore": False,
        }
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            config_path.chmod(0o600)
        except OSError:
            pass
        self._stderr_file = stderr_path.open("ab")
        try:
            stderr_path.chmod(0o600)
        except OSError:
            pass
        environment = sanitized_child_environment(
            {
                "OPENCODEX_HOME": str(self.state_dir),
                "CODEX_HOME": str(self.state_dir / "codex-home"),
                "OCX_SERVICE": "1",
                "OPENCODEX_CODEX_SHIM_AUTO_RESTORE": "0",
                HARNESS_GATEWAY_ENV_KEY: self.upstream_api_key,
            },
        )
        try:
            self.process = subprocess.Popen(
                [self.executable, "start", "--port", str(self.port)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=self._stderr_file,
                env=environment,
                start_new_session=os.name != "nt",
            )
            deadline = time.monotonic() + max(0.1, float(timeout_seconds))
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise NativeHarnessUnavailable(
                        "OpenCodex gateway stopped before becoming ready."
                    )
                try:
                    with urlopen(
                        f"http://127.0.0.1:{self.port}/healthz",
                        timeout=0.5,
                    ) as response:
                        if 200 <= int(response.status) < 300:
                            return
                except OSError:
                    time.sleep(0.1)
            raise NativeHarnessUnavailable(
                "OpenCodex gateway did not become ready before the startup deadline."
            )
        except Exception:
            self.stop()
            raise

    def stop(self, *, timeout_seconds: float = 3.0) -> None:
        process = self.process
        self.process = None
        if process is not None and process.poll() is None:
            try:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
                process.wait(timeout=max(0.1, float(timeout_seconds)))
            except (ProcessLookupError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=max(0.1, float(timeout_seconds)))
        if self._stderr_file is not None:
            self._stderr_file.close()
            self._stderr_file = None

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process is not None else None


class NativeHarnessRuntime:
    """Keep gateway and native harness lifetime aligned with one room runtime."""

    def __init__(self, delegate, *, harness: str, gateway: OpenCodexHarnessGateway | None) -> None:
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
        return {
            **dict(details),
            "execution_harness": self.harness,
            "harness_gateway_pid": self.gateway.pid if self.gateway else None,
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
):
    selected = clean_room_text(harness, limit=32)
    if selected not in {"codex", "claude"}:
        raise NativeHarnessUnavailable(f"Unsupported native harness: {selected}")
    gateway: OpenCodexHarnessGateway | None = None
    harness_endpoint = provider_endpoint
    if not _supports_direct_harness(provider_kind, selected):
        gateway = OpenCodexHarnessGateway(
            state_dir=Path(runtime_state_dir) / "opencodex",
            upstream_base_url=provider_endpoint,
            upstream_api_key=credential,
            model=model,
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


def _resolve_opencodex_executable() -> str:
    return shutil.which("ocx") or shutil.which("opencodex") or ""


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _is_loopback_url(value: str) -> bool:
    hostname = (urlsplit(value).hostname or "").casefold()
    return hostname in {"127.0.0.1", "::1", "localhost"}


__all__ = [
    "NativeHarnessRuntime",
    "NativeHarnessUnavailable",
    "OpenCodexHarnessGateway",
    "native_harness_runtime",
]
