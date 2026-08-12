"""OpenCode serve harness that points only at a session-local NativeModelGateway."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from agentsassemble.providers.native_harness import NativeHarnessRuntime, NativeHarnessUnavailable
from agentsassemble.providers.native_harness_gateway import NativeModelGateway
from agentsassemble.providers.opencode import OpenCodeRuntime
from agentsassemble.providers.opencode_server import OpenCodeServerProcess
from agentsassemble.providers.room_portal import RoomPortal
from agentsassemble.room.text import clean_room_text


def create_opencode_harness_runtime(
    *,
    agent_id: str,
    runtime_kind: str,
    provider_kind: str,
    provider_endpoint: str,
    credential: str,
    model: str,
    reasoning_effort: str,
    permission_mode: str,
    workspace: str,
    runtime_state_dir: str,
    environment: dict[str, str] | None,
    room_portal: RoomPortal | None,
    request_headers: tuple[tuple[str, str], ...] = (),
    variant: str = "",
    max_output_tokens: int = 0,
    context_contract_bytes: int = 256_000,
) -> NativeHarnessRuntime:
    del reasoning_effort  # OpenCode variant/model selection owns effort-like knobs.
    executable = shutil.which("opencode")
    if not executable:
        raise NativeHarnessUnavailable("OpenCode CLI is not installed.")
    state_dir = Path(runtime_state_dir).expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    gateway = NativeModelGateway(
        upstream_base_url=provider_endpoint,
        upstream_api_key=credential,
        model=model,
        provider_kind=provider_kind,
        variant=variant,
        max_output_tokens=max_output_tokens,
        request_headers=request_headers,
        context_contract_bytes=context_contract_bytes,
        state_dir=str(state_dir / "gateway"),
    )
    config_dir = state_dir / "opencode-config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "opencode.json"
    opencode_model = _opencode_harness_model_id(model)
    config_path.write_text(
        json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "provider": {
                    "agentsassemble": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "AgentsAssemble",
                        "options": {
                            "baseURL": "__AGENTSASSEMBLE_GATEWAY_BASE_URL__",
                            "apiKey": "agentsassemble-local-gateway",
                        },
                        "models": {
                            opencode_model: {
                                "name": clean_room_text(model, limit=128) or opencode_model,
                            }
                        },
                    }
                },
                "model": f"agentsassemble/{opencode_model}",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    delegate = _OpenCodeHarnessDelegate(
        agent_id=agent_id,
        gateway=gateway,
        executable=executable,
        workspace=workspace,
        state_dir=state_dir,
        config_path=config_path,
        config_dir=config_dir,
        model=f"agentsassemble/{opencode_model}",
        upstream_model=model,
        variant=variant,
        permission_mode=permission_mode,
        environment=environment,
        room_portal=room_portal,
    )
    return NativeHarnessRuntime(
        delegate,
        harness="opencode",
        runtime_kind=runtime_kind,
        gateway=gateway,
    )


class _OpenCodeHarnessDelegate:
    """Own a session-private OpenCode server with gateway-bound config only."""

    def __init__(
        self,
        *,
        agent_id: str,
        gateway: NativeModelGateway,
        executable: str,
        workspace: str,
        state_dir: Path,
        config_path: Path,
        config_dir: Path,
        model: str,
        upstream_model: str,
        variant: str,
        permission_mode: str,
        environment: dict[str, str] | None,
        room_portal: RoomPortal | None,
    ) -> None:
        self._agent_id = agent_id
        self._gateway = gateway
        self._executable = executable
        self._server: OpenCodeServerProcess | None = None
        self._workspace = workspace
        self._state_dir = state_dir
        self._config_path = config_path
        self._config_dir = config_dir
        self._model = model
        self._upstream_model = clean_room_text(upstream_model, limit=256)
        self._variant = variant
        self._permission_mode = permission_mode
        self._environment = dict(environment or {})
        self._room_portal = room_portal
        self._runtime: OpenCodeRuntime | None = None
        self._provider_request_handler = None

    def set_request_handler(self, handler) -> None:
        self._provider_request_handler = handler
        if self._runtime is not None:
            self._runtime.set_request_handler(handler)

    def start(self) -> dict[str, object]:
        if self._runtime is not None:
            return self.health()
        self._gateway.start()
        self._rewrite_config_base_url(self._gateway.endpoint)
        xdg_config = self._state_dir / "xdg-config"
        xdg_data = self._state_dir / "xdg-data"
        xdg_config.mkdir(parents=True, exist_ok=True)
        xdg_data.mkdir(parents=True, exist_ok=True)
        # Session-only config/data homes so global operator OpenCode state is untouched.
        self._server = OpenCodeServerProcess(
            cwd=self._workspace,
            executable=self._executable,
            environment={
                **self._environment,
                "OPENCODE_CONFIG": str(self._config_path),
                "OPENCODE_CONFIG_DIR": str(self._config_dir),
                "XDG_CONFIG_HOME": str(xdg_config),
                "XDG_DATA_HOME": str(xdg_data),
            },
        )
        self._server.start()
        if not self._server.endpoint:
            raise NativeHarnessUnavailable("OpenCode harness server failed to start.")
        self._runtime = OpenCodeRuntime(
            self._agent_id,
            endpoint=self._server.endpoint,
            workspace=self._workspace,
            state_dir=self._state_dir / "session",
            model=self._model,
            variant=self._variant,
            permission_mode=self._permission_mode,
            server_pid=getattr(self._server.process, "pid", None),
            room_portal=self._room_portal,
        )
        if self._provider_request_handler is not None:
            self._runtime.set_request_handler(self._provider_request_handler)
        return self._runtime.start()

    def send(self, text: str) -> None:
        self.start()
        assert self._runtime is not None
        self._runtime.send(text)

    def send_room_observation(self, text: str, *, media_blocks=None) -> None:
        del media_blocks
        self.send(text)

    def read_output(self, *, timeout_seconds: float, on_delta=None, on_activity=None):
        self.start()
        assert self._runtime is not None
        result = self._runtime.read_output(
            timeout_seconds=timeout_seconds,
            on_delta=on_delta,
            on_activity=on_activity,
        )
        metadata = result.get("metadata")
        if (
            isinstance(metadata, dict)
            and metadata.get("observed_model_id") == self._model
        ):
            result = {
                **result,
                "metadata": {
                    **metadata,
                    "observed_model_id": self._upstream_model,
                },
            }
        return result

    def interrupt(self) -> None:
        if self._runtime is not None:
            self._runtime.interrupt()

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        del timeout_seconds
        try:
            if self._runtime is not None:
                self._runtime.stop()
        finally:
            self._runtime = None
            if self._server is not None:
                self._server.stop()
                self._server = None

    def health(self) -> dict[str, object]:
        if self._runtime is None:
            return {
                "running": False,
                "runtime_kind": "opencode",
                "execution_harness": "opencode",
                "unsupported": ["global_opencode_config_mutation"],
            }
        payload = dict(self._runtime.health())
        payload["execution_harness"] = "opencode"
        payload["unsupported"] = ["global_opencode_config_mutation"]
        return payload

    def _rewrite_config_base_url(self, endpoint: str) -> None:
        document = json.loads(self._config_path.read_text(encoding="utf-8"))
        provider = document.setdefault("provider", {}).setdefault("agentsassemble", {})
        options = provider.setdefault("options", {})
        options["baseURL"] = str(endpoint or "").rstrip("/")
        self._config_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _opencode_harness_model_id(model: str) -> str:
    clean = clean_room_text(model, limit=128) or "upstream"
    # OpenCode model ids are path segments under provider/; keep them simple.
    return "".join(
        character if character.isalnum() or character in {"-", "_", "."} else "-"
        for character in clean
    )[:96] or "upstream"


__all__ = ["create_opencode_harness_runtime"]
