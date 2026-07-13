from __future__ import annotations

import getpass
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from agentsassemble.native_cli_providers import native_cli_provider_definition
from agentsassemble.codex_app_server_live_runtime import CodexAppServerLiveRuntime
from agentsassemble.opencode_runtime import OpenCodeServerProcess
from agentsassemble.provider_runtime_config import ProviderRuntimeConfig
from agentsassemble.provider_secrets import PROVIDER_SECRETS
from agentsassemble.room_agent_bridge import RoomAgentBridge, runtime_from_config
from agentsassemble.ws_room_client import connect_room_ws, join_room_session


class AgentAttendee:
    """Provider-side owner of an invite token, canonical WebSocket, and native session."""

    def __init__(
        self,
        *,
        invite_url: str,
        provider_id: str,
        display_name: str = "",
        workspace: str = "",
        model: str = "",
        reasoning_effort: str = "",
        service_tier: str = "",
        variant: str = "",
        permission_mode: str = "meeting_read_only",
    ) -> None:
        self.server_url, self.invite_token = parse_agent_invite_url(invite_url)
        definition = native_cli_provider_definition(provider_id)
        if definition is None:
            raise ValueError(f"Unsupported Agent Session provider: {provider_id}")
        self.definition = definition
        self.display_name = display_name or definition.display_name
        self._workspace_argument = workspace
        self.model = model or definition.default_model
        self.reasoning_effort = reasoning_effort or definition.default_reasoning_effort
        self.service_tier = service_tier or definition.default_service_tier
        self.variant = variant or definition.default_variant
        self.permission_mode = permission_mode or definition.default_permission_mode
        self._stop = threading.Event()
        self._bridge: RoomAgentBridge | None = None
        self._runtime = None
        self._opencode_server: OpenCodeServerProcess | None = None

    def run(self) -> int:
        temporary = tempfile.TemporaryDirectory(prefix="agentsassemble-attendee-") if not self._workspace_argument else None
        workspace = Path(self._workspace_argument or temporary.name).expanduser().resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        session_token = ""
        try:
            joined = join_room_session(
                self.server_url,
                self.invite_token,
                display_name=self.display_name,
                participant_type="agent",
                device_token=f"agent-attendee-{int(time.time() * 1000)}",
                timeout=10.0,
            )
            expected_kind = str(joined.get("provider_kind") or "manual")
            if expected_kind not in {"manual", self.definition.provider_kind}:
                raise ValueError("The invite is assigned to a different provider kind.")
            session_token = str(joined["session_token"])
            participant_id = str(joined.get("agent_id") or "")
            room_id = str(joined.get("meeting_id") or "")
            self._runtime = self._build_runtime(participant_id, workspace)
            orientation = _orientation_text(joined.get("guide"))
            while not self._stop.is_set():
                client = connect_room_ws(self.server_url, session_token, ["room_events"], timeout=10.0)
                try:
                    client.sock.settimeout(0.25)
                except (AttributeError, OSError):
                    pass
                bridge = RoomAgentBridge(
                    client,
                    self._runtime,
                    room_id=room_id,
                    participant_id=participant_id,
                    session_id=participant_id,
                    initial_orientation=orientation,
                    stop_runtime_on_exit=False,
                )
                self._bridge = bridge
                bridge.run()
                orientation = ""
                self._bridge = None
                if not self._stop.wait(1.0):
                    continue
            return 0
        finally:
            if self._runtime is not None:
                try:
                    self._runtime.stop(timeout_seconds=2.0)
                except Exception:
                    pass
            if self._opencode_server is not None:
                self._opencode_server.stop()
            if session_token:
                _leave_room(self.server_url, session_token)
            if temporary is not None:
                temporary.cleanup()

    def stop(self) -> None:
        self._stop.set()
        bridge = self._bridge
        if bridge is not None:
            bridge.stop()

    def _build_runtime(self, participant_id: str, workspace: Path):
        spec = self.definition.make_selected_spec(
            agent_id=participant_id,
            display_name=self.display_name,
            cwd=workspace,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            service_tier=self.service_tier,
            variant=self.variant,
            permission_mode=self.permission_mode,
        )
        if self.definition.provider_id == "codex":
            return CodexAppServerLiveRuntime(
                participant_id,
                workspace=str(workspace),
                model=spec.model,
                reasoning_effort=spec.reasoning_effort,
                permission_mode=spec.permission_mode,
            )
        command = list(spec.command)
        state_dir = workspace / ".agentsassemble-attendee" / participant_id
        config: dict[str, object] = {
            "participant_id": participant_id,
            "provider_kind": spec.normalized_provider_kind(),
            "command": command,
            "cwd": str(workspace),
            "model": spec.model,
            "reasoning_effort": spec.reasoning_effort,
            "service_tier": spec.service_tier,
            "variant": spec.variant,
            "permission_mode": spec.permission_mode,
            "transport": spec.transport,
            "runtime_state_dir": str(state_dir),
            "quiet_seconds": spec.quiet_seconds,
            "input_mode": spec.input_mode,
            "submit_newline": spec.submit_newline,
            "submit_delay_seconds": spec.submit_delay_seconds,
            "terminal_rows": spec.terminal_rows,
            "terminal_columns": spec.terminal_columns,
            "startup_quiet_seconds": spec.startup_quiet_seconds,
            "startup_timeout_seconds": spec.startup_timeout_seconds,
            "startup_accept_contains": spec.startup_accept_contains,
            "startup_accept_keys": spec.startup_accept_keys,
            "startup_ready_contains": spec.startup_ready_contains,
            "startup_input": spec.startup_input,
            "provider_endpoint": "",
            "provider_server_pid": None,
        }
        credential = ""
        if spec.normalized_provider_kind() == "opencode_server":
            self._opencode_server = OpenCodeServerProcess(cwd=state_dir / "server")
            health = self._opencode_server.start()
            config["provider_endpoint"] = health["endpoint"]
            config["provider_server_pid"] = health["pid"]
        elif spec.normalized_provider_kind() == "deepseek_api":
            credential = PROVIDER_SECRETS.get("deepseek")
            if not credential:
                raise RuntimeError("credential_missing")
        return runtime_from_config(ProviderRuntimeConfig.parse_strict(config), credential=credential)


def read_hidden_invite_url() -> str:
    if sys.stdin.isatty():
        return getpass.getpass("Invite URL: ").strip()
    return sys.stdin.readline(8192).strip()


def parse_agent_invite_url(value: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        raise ValueError("Invite URL is invalid.") from None
    token = str(parse_qs(parsed.query).get("token", [""])[0] or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not token:
        raise ValueError("Invite URL must be an HTTP(S) /join URL containing a token.")
    path = parsed.path
    if path.endswith("/join"):
        path = path[: -len("/join")]
    elif path.endswith("/join/"):
        path = path[: -len("/join/")]
    server_url = urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/"), "", ""))
    return server_url, token


def _orientation_text(value: object) -> str:
    guide = value if isinstance(value, dict) else {}
    lines = [str(guide.get("welcome") or "You joined a shared AgentsAssemble room.")]
    lines.extend(str(item) for item in list(guide.get("how_to") or []) if isinstance(item, str))
    lines.extend(str(item) for item in list(guide.get("etiquette") or []) if isinstance(item, str))
    return "Room attendee guide:\n" + "\n".join(f"- {line}" for line in lines if line)


def _leave_room(server_url: str, session_token: str) -> None:
    try:
        request = Request(
            f"{server_url.rstrip('/')}/api/room-invite/leave",
            data=b"{}",
            headers={"Authorization": f"Bearer {session_token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5.0):
            return
    except Exception:
        return


def run_attendee_from_cli(**kwargs: object) -> int:
    invite_url = read_hidden_invite_url()
    attendee = AgentAttendee(invite_url=invite_url, **kwargs)

    def stop(_signum, _frame) -> None:
        attendee.stop()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop)
    print("Agent Session joined; room events are delivered over the canonical WebSocket.", flush=True)
    return attendee.run()
