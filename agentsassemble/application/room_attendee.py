from __future__ import annotations

import getpass
import os
import signal
import sys
import tempfile
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from agentsassemble.diagnostics.cleanup import CleanupReport, emit_cleanup_failure
from agentsassemble.providers.launch_specs import native_cli_provider_definition
from agentsassemble.providers.local_openai import local_openai_endpoint
from agentsassemble.providers.codex_app_server_live import CodexAppServerLiveRuntime
from agentsassemble.providers.opencode import OpenCodeServerProcess
from agentsassemble.providers.remote_openai import remote_openai_endpoint
from agentsassemble.providers.runtime_config import ProviderRuntimeConfig, ProviderRuntimeProfile
from agentsassemble.providers.runtime_factory import runtime_from_config
from agentsassemble.providers.room_portal import RoomPortal, room_session_orientation
from agentsassemble.providers.room_portal_search import RoomPortalSearchBroker
from agentsassemble.providers.secrets import (
    PROVIDER_SECRETS,
    secret_provider_id_for_kind,
    validate_provider_secret,
)
from agentsassemble.providers.agent_bridge import RoomAgentBridge
from agentsassemble.providers.redacting_room_client import CredentialRedactingRoomClient
from agentsassemble.web.room_client import connect_room_ws, join_agent_room_session


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
        max_output_tokens: int = 0,
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
        self.max_output_tokens = (
            max_output_tokens or definition.default_max_output_tokens
        )
        self._stop = threading.Event()
        self._bridge: RoomAgentBridge | None = None
        self._runtime = None
        self._runtime_profile: ProviderRuntimeProfile | None = None
        self._opencode_server: OpenCodeServerProcess | None = None
        self._provider_credential = ""
        self.last_cleanup_report = CleanupReport("agent_attendee")

    def run(self) -> int:
        temporary = tempfile.TemporaryDirectory(prefix="agentsassemble-attendee-") if not self._workspace_argument else None
        portal_temporary = tempfile.TemporaryDirectory(prefix="agentsassemble-room-portal-")
        workspace = Path(self._workspace_argument or temporary.name).expanduser().resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        session_token = ""
        search_broker: RoomPortalSearchBroker | None = None
        try:
            joined = join_agent_room_session(
                self.server_url,
                self.invite_token,
                display_name=self.display_name,
                provider_kind=self.definition.provider_kind,
                timeout=10.0,
            )
            expected_kind = str(joined.get("provider_kind") or "manual")
            if expected_kind == "manual":
                raise ValueError(
                    "Agent Session invites must name the provider that is allowed to connect."
                )
            if expected_kind != self.definition.provider_kind:
                raise ValueError("The invite is assigned to a different provider kind.")
            session_token = str(joined["session_token"])
            participant_id = str(joined.get("agent_id") or "")
            room_id = str(joined.get("meeting_id") or "")
            portal = RoomPortal(
                Path(portal_temporary.name),
                participant_id=participant_id,
            )
            portal.prepare()
            search_broker = RoomPortalSearchBroker(
                portal.root,
                server_url=self.server_url,
                session_token=session_token,
                room_id=room_id,
                tool_allowed=portal.tool_allowed,
            )
            search_broker.start()
            environment = portal.provider_environment(os.environ.get("PATH", ""))
            self._runtime = self._build_runtime(
                participant_id,
                workspace,
                environment=environment,
                room_portal=portal,
            )
            orientation = _orientation_text(
                joined.get("guide"),
                self.definition.provider_kind,
            )
            while not self._stop.is_set():
                client = connect_room_ws(self.server_url, session_token, ["room_events"], timeout=10.0)
                bridge = RoomAgentBridge(
                    CredentialRedactingRoomClient(
                        client,
                        sensitive_values=(
                            self.invite_token,
                            session_token,
                            self._provider_credential,
                        ),
                    ),
                    self._runtime,
                    room_id=room_id,
                    participant_id=participant_id,
                    session_id=participant_id,
                    initial_orientation=orientation,
                    stop_runtime_on_exit=False,
                    runtime_profile=self._runtime_profile,
                    room_portal=portal,
                )
                self._bridge = bridge
                if bridge.run() != 0:
                    raise RuntimeError("Agent Bridge cleanup failed.")
                if bridge.remote_stop_requested:
                    # A successful remote-stop bridge run already stopped and
                    # verified the provider runtime before reporting completion.
                    self._runtime = None
                    break
                orientation = ""
                self._bridge = None
                if not self._stop.wait(1.0):
                    continue
        finally:
            search_broker_error: Exception | None = None
            if search_broker is not None:
                try:
                    search_broker.stop()
                except Exception as error:
                    search_broker_error = error
            self.last_cleanup_report = self._cleanup(
                session_token=session_token,
                temporary=temporary,
                portal_temporary=portal_temporary,
            )
            if search_broker_error is not None:
                self.last_cleanup_report.record_failure(
                    "room_search_broker.stop",
                    search_broker_error,
                    handle_id="room-search-broker",
                    orphaned=True,
                )
            emit_cleanup_failure(self.last_cleanup_report)
        return 0 if self.last_cleanup_report.ok else 1

    def stop(self) -> None:
        self._stop.set()
        bridge = self._bridge
        if bridge is not None:
            bridge.stop()

    def _cleanup(
        self,
        *,
        session_token: str,
        temporary: object | None,
        portal_temporary: object | None = None,
    ) -> CleanupReport:
        cleanup = CleanupReport("agent_attendee")
        if self._runtime is not None:
            try:
                self._runtime.stop(timeout_seconds=2.0)
                cleanup.record_success()
            except Exception as error:
                cleanup.record_failure(
                    "runtime.stop",
                    error,
                    handle_id="provider-runtime",
                    orphaned=_runtime_still_running(self._runtime),
                )
        if self._opencode_server is not None:
            try:
                self._opencode_server.stop()
                cleanup.record_success()
            except Exception as error:
                process = self._opencode_server.process
                cleanup.record_failure(
                    "opencode_server.stop",
                    error,
                    handle_id="opencode-server",
                    orphaned=process is not None and process.poll() is None,
                )
        if session_token:
            try:
                _leave_room(self.server_url, session_token)
                cleanup.record_success()
            except Exception as error:
                cleanup.record_failure("room.leave", error, handle_id="room-session")
        if temporary is not None:
            try:
                temporary.cleanup()
                cleanup.record_success()
            except Exception as error:
                cleanup.record_failure("workspace.cleanup", error, handle_id="temporary-workspace")
        if portal_temporary is not None:
            try:
                portal_temporary.cleanup()
                cleanup.record_success()
            except Exception as error:
                cleanup.record_failure("room_portal.cleanup", error, handle_id="room-portal")
        return cleanup

    def _build_runtime(
        self,
        participant_id: str,
        workspace: Path,
        *,
        environment: dict[str, str] | None = None,
        room_portal: RoomPortal | None = None,
    ):
        spec = self.definition.make_selected_spec(
            agent_id=participant_id,
            display_name=self.display_name,
            cwd=workspace,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            service_tier=self.service_tier,
            variant=self.variant,
            permission_mode=self.permission_mode,
            max_output_tokens=self.max_output_tokens,
        )
        self._runtime_profile = ProviderRuntimeProfile(
            provider_kind=spec.normalized_provider_kind(),
            runtime_kind=spec.runtime_kind,
            model=spec.model,
            reasoning_effort=spec.reasoning_effort,
            service_tier=spec.service_tier,
            variant=spec.variant,
            execution_harness=spec.execution_harness,
            permission_mode=spec.permission_mode,
            max_output_tokens=spec.max_output_tokens,
            transport=spec.transport,
        )
        if self.definition.provider_id == "codex":
            return CodexAppServerLiveRuntime(
                participant_id,
                workspace=str(workspace),
                model=spec.model,
                reasoning_effort=spec.reasoning_effort,
                permission_mode=spec.permission_mode,
                service_tier=spec.service_tier,
                room_portal=room_portal,
            )
        command = list(spec.command)
        state_dir = workspace / ".agentsassemble-attendee" / participant_id
        config: dict[str, object] = {
            "participant_id": participant_id,
            "provider_kind": spec.normalized_provider_kind(),
            "runtime_kind": spec.runtime_kind,
            "command": command,
            "cwd": str(workspace),
            "model": spec.model,
            "reasoning_effort": spec.reasoning_effort,
            "service_tier": spec.service_tier,
            "variant": spec.variant,
            "execution_harness": spec.execution_harness,
            "permission_mode": spec.permission_mode,
            "max_output_tokens": spec.max_output_tokens,
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
        else:
            config["provider_endpoint"] = local_openai_endpoint(
                spec.normalized_provider_kind()
            ) or remote_openai_endpoint(spec.normalized_provider_kind())
            secret_provider_id = secret_provider_id_for_kind(
                spec.normalized_provider_kind()
            )
            credential = (
                PROVIDER_SECRETS.get(secret_provider_id)
                if secret_provider_id
                else ""
            )
            if secret_provider_id and not credential:
                raise RuntimeError("credential_missing")
            if credential:
                credential = validate_provider_secret(credential)
        self._provider_credential = credential
        runtime_kwargs: dict[str, object] = {"credential": credential}
        if environment is not None:
            runtime_kwargs["environment"] = environment
        if room_portal is not None:
            runtime_kwargs["room_portal"] = room_portal
        return runtime_from_config(
            ProviderRuntimeConfig.parse_strict(config),
            **runtime_kwargs,
        )


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


def _orientation_text(value: object, provider_kind: object = "") -> str:
    guide = value if isinstance(value, dict) else {}
    welcome = str(guide.get("welcome") or "You joined a shared AgentsAssemble room.")
    return (
        f"Room attendee guide:\n- {welcome}\n\n"
        f"{room_session_orientation(provider_kind)}"
    )


def _leave_room(server_url: str, session_token: str) -> None:
    request = Request(
        f"{server_url.rstrip('/')}/api/room-invite/leave",
        data=b"{}",
        headers={"Authorization": f"Bearer {session_token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5.0):
            return
    except HTTPError as error:
        if error.code in {401, 403}:
            error.close()
            return
        raise


def _runtime_still_running(runtime: object) -> bool:
    health = getattr(runtime, "health", None)
    if not callable(health):
        return True
    try:
        return bool(health().get("running", True))
    except Exception:
        return True


def run_attendee_from_cli(**kwargs: object) -> int:
    invite_url = read_hidden_invite_url()
    attendee = AgentAttendee(invite_url=invite_url, **kwargs)

    def stop(_signum, _frame) -> None:
        attendee.stop()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop)
    print("Agent Session joined; room events are delivered over the canonical WebSocket.", flush=True)
    return attendee.run()
