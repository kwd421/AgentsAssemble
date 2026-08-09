"""Server-owned provider Agent Bridge process lifecycle."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping
from uuid import uuid4

from agentsassemble.diagnostics.cleanup import CleanupReport
from agentsassemble.diagnostics.sensitive_text import (
    redact_persisted_diagnostic_bytes,
    validate_redactable_sensitive_value,
)
from agentsassemble.providers.bridge_sensitive_values import BridgeSensitiveValueRegistry
from agentsassemble.providers.bridge_launch_secrets import encode_secure_launch_payload
from agentsassemble.providers.launch_specs import (
    NativeCliProviderSpec,
    validate_native_cli_provider_spec,
)
from agentsassemble.providers.local_openai import local_openai_endpoint
from agentsassemble.providers.opencode import OpenCodeServerProcess
from agentsassemble.providers.process_environment import sanitized_child_environment
from agentsassemble.providers.remote_openai import remote_openai_endpoint
from agentsassemble.providers.room_portal import RoomPortal
from agentsassemble.providers.runtime_config import CanonicalBridgeLaunchConfig
from agentsassemble.providers.secrets import (
    PROVIDER_SECRETS,
    secret_provider_id_for_kind,
    validate_provider_secret,
)


BridgeExitListener = Callable[[str, str, int, str], None]
_PERSISTED_STDERR_LIMIT = 16_000


def _raw_stderr_tail_limit(handle: _BridgeHandle) -> int:
    longest_sensitive_value = max(
        (
            len(value.encode("utf-8", errors="replace"))
            for value in handle.sensitive_values
        ),
        default=1,
    )
    return _PERSISTED_STDERR_LIMIT + max(0, longest_sensitive_value - 1)


def _default_provider_executable(executable: str) -> str | None:
    resolved = shutil.which(executable)
    if Path(executable).name.casefold() != "codex":
        return resolved
    candidates = [
        Path(resolved) if resolved else None,
        Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
    ]
    for candidate in candidates:
        if candidate is None or not candidate.is_file():
            continue
        try:
            check = subprocess.run(
                [str(candidate), "--version"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if check.returncode == 0:
            return str(candidate)
    return None


@dataclass
class _BridgeHandle:
    handle_id: str
    room_id: str
    session_id: str
    runtime_profile_key: str
    resolved_executable: str
    process: subprocess.Popen[bytes]
    config_path: Path
    stdout_path: Path
    stderr_path: Path
    stopping: bool = False
    stderr_byte_count: int = 0
    stderr_line_count: int = 0
    stderr_warning_count: int = 0
    stderr_tail_truncated: bool = False
    stderr_tail: bytearray = field(default_factory=bytearray)
    stderr_lock: threading.Lock = field(default_factory=threading.Lock)
    stderr_thread: threading.Thread | None = None
    provider_process_shared: bool = False
    sensitive_values: tuple[str, ...] = ()


@dataclass
class _BridgeLaunchOwnership:
    lock: threading.Lock = field(default_factory=threading.Lock)
    users: int = 0


class NativeCliBridgeProcessManager:
    """Launches server-owned Agent Bridge processes without exposing tickets."""

    def __init__(
        self,
        output_root: str | Path,
        *,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        executable_resolver: Callable[[str], str | None] = _default_provider_executable,
        secret_resolver: Callable[[str], str] = PROVIDER_SECRETS.get,
        on_exit: BridgeExitListener | None = None,
    ) -> None:
        self.output_root = Path(output_root)
        self._popen_factory = popen_factory
        self._resolve_executable = executable_resolver
        self._secret_resolver = secret_resolver
        self._on_exit = on_exit
        self._lock = threading.RLock()
        self._handles: dict[tuple[str, str], _BridgeHandle] = {}
        self._sensitive_values = BridgeSensitiveValueRegistry()
        self._launch_ownership: dict[tuple[str, str], _BridgeLaunchOwnership] = {}
        self._opencode_server: OpenCodeServerProcess | None = None
        self.last_cleanup_report = CleanupReport("native_cli_bridge_process_manager")

    def set_exit_listener(self, listener: BridgeExitListener | None) -> None:
        with self._lock:
            self._on_exit = listener

    def adopt_preserved_shared_runtime(
        self,
        room_id: str,
        session: Mapping[str, object],
    ) -> bool:
        """Own a shared provider process kept alive by a rolling handoff."""

        if str(session.get("provider_kind") or "") != "opencode_server":
            return False
        if str(session.get("process_ownership") or "") != "server":
            return False
        if str(session.get("runtime_status") or "") in {"", "stopped"}:
            return False
        session_id = str(session.get("session_id") or session.get("participant_id") or "")
        runtime_profile_key = str(session.get("runtime_profile_key") or "")
        if not session_id or not runtime_profile_key:
            raise RuntimeError("Preserved OpenCode session is missing its runtime profile.")
        config_path = (
            self.output_root
            / "rooms"
            / room_id
            / "bridges"
            / session_id
            / runtime_profile_key
            / "config.json"
        )
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "Preserved OpenCode session has no valid bridge configuration."
            ) from error
        config = CanonicalBridgeLaunchConfig.parse_strict(raw)
        if config.room_id != room_id or config.session_id != session_id:
            raise RuntimeError("Preserved OpenCode bridge configuration belongs to another session.")
        pid = config.runtime.provider_server_pid
        endpoint = config.runtime.provider_endpoint
        if pid is None:
            raise RuntimeError("Preserved OpenCode session did not record its shared server PID.")
        with self._lock:
            current = self._opencode_server
            if current is not None:
                current_pid = current.process.pid if current.process is not None else None
                if current_pid != pid or current.endpoint != endpoint:
                    # A record left behind by an older server, not a conflict to
                    # resolve. Refusing the whole adoption here aborted the
                    # rolling replacement's boot, so one dead bridge could veto
                    # every future roll; that session recovers normally instead.
                    return False
                return True
            try:
                self._opencode_server = OpenCodeServerProcess.adopt(
                    cwd=self.output_root,
                    executable=config.runtime.command[0],
                    pid=pid,
                    endpoint=endpoint,
                )
            except RuntimeError:
                # The recorded process is gone or unhealthy. Leave the slot open
                # for a session whose server is still up.
                return False
        return True

    def start(
        self,
        room_id: str,
        session: dict[str, object],
        spec: NativeCliProviderSpec,
        *,
        server_url: str = "",
        ticket_issuer: Callable[[dict[str, object]], object] | None = None,
    ) -> dict[str, object]:
        validate_native_cli_provider_spec(spec)
        session_id = str(session.get("session_id") or spec.agent_id)
        runtime_profile_key = spec.runtime_profile_key()
        key = (room_id, session_id)
        with self._own_bridge_launch(key):
            return self._start_owned_bridge(
                room_id,
                session,
                spec,
                server_url=server_url,
                ticket_issuer=ticket_issuer,
                session_id=session_id,
                runtime_profile_key=runtime_profile_key,
            )

    def _start_owned_bridge(
        self,
        room_id: str,
        session: dict[str, object],
        spec: NativeCliProviderSpec,
        *,
        server_url: str,
        ticket_issuer: Callable[[dict[str, object]], object] | None,
        session_id: str,
        runtime_profile_key: str,
    ) -> dict[str, object]:
        key = (room_id, session_id)
        with self._lock:
            existing = self._handles.get(key)
            if existing is not None and existing.process.poll() is None:
                if existing.runtime_profile_key != runtime_profile_key:
                    raise RuntimeError(
                        "Agent Bridge is already running with an incompatible runtime profile; stop it before restarting."
                    )
                return self._launch_payload(existing, spec, runtime_reused=True)
        if not server_url:
            raise ValueError("Agent Bridge server URL is required.")
        if ticket_issuer is None:
            raise ValueError("Agent Bridge ticket issuer is required.")
        credential = ""
        provider_endpoint = ""
        provider_server_pid: int | None = None
        secret_provider_id = secret_provider_id_for_kind(
            spec.normalized_provider_kind()
        )
        local_provider_endpoint = local_openai_endpoint(
            spec.normalized_provider_kind()
        )
        remote_provider_endpoint = remote_openai_endpoint(
            spec.normalized_provider_kind()
        )
        if secret_provider_id:
            resolved_credential = self._secret_resolver(secret_provider_id)
            if not resolved_credential:
                raise RuntimeError("credential_missing")
            try:
                credential = validate_provider_secret(resolved_credential)
            except ValueError as error:
                raise RuntimeError("credential_invalid") from error
            executable = "server-owned-api"
            provider_endpoint = spec.provider_endpoint or remote_provider_endpoint
        elif local_provider_endpoint:
            executable = spec.command[0]
            provider_endpoint = local_provider_endpoint
        else:
            executable = self._resolve(spec.command[0] if spec.command else "")
        if not executable:
            raise FileNotFoundError(f"configured command missing: {spec.command[0] if spec.command else ''}")
        if spec.normalized_provider_kind() == "opencode_server":
            opencode = self._ensure_opencode_server(executable)
            provider_endpoint = opencode.endpoint
            provider_server_pid = opencode.process.pid if opencode.process is not None else None
        identity = {
            "agent_id": spec.agent_id,
            "display_name": spec.display_name,
            "participant_type": "agent",
            "client_type": "agent_bridge",
            "connection_kind": "native_cli_bridge",
            "invite_scope": "read_write",
            "meeting_id": room_id,
            "session_id": session_id,
            "provider_kind": spec.normalized_provider_kind(),
            "runtime_kind": spec.runtime_kind,
            "operator": False,
        }
        issued_connection = ticket_issuer(identity)
        if isinstance(issued_connection, Mapping):
            ticket = str(issued_connection.get("ticket") or "")
            session_token = str(issued_connection.get("session_token") or "")
        else:
            ticket = str(issued_connection or "")
            session_token = ""
        ticket = validate_redactable_sensitive_value(
            ticket,
            label="Agent Bridge ticket",
        )
        if session_token:
            session_token = validate_redactable_sensitive_value(
                session_token,
                label="Agent Bridge session token",
            )
        bridge_dir = self.output_root / "rooms" / room_id / "bridges" / session_id
        profile_dir = bridge_dir / runtime_profile_key
        profile_dir.mkdir(parents=True, exist_ok=True)
        bridge_launch_id = f"bridge-{uuid4().hex}"
        config_path = profile_dir / "config.json"
        stdout_path = profile_dir / "stdout.log"
        stderr_path = profile_dir / "stderr.log"
        config = {
            "room_id": room_id,
            "participant_id": spec.agent_id,
            "session_id": session_id,
            "bridge_launch_id": bridge_launch_id,
            "provider_kind": spec.normalized_provider_kind(),
            "runtime_kind": spec.runtime_kind,
            "command": (
                [executable, *spec.command[1:]]
                if not secret_provider_id
                else list(spec.command)
            ),
            "cwd": spec.cwd,
            "model": spec.model,
            "reasoning_effort": spec.reasoning_effort,
            "service_tier": spec.service_tier,
            "variant": spec.variant,
            "execution_harness": spec.execution_harness,
            "permission_mode": spec.permission_mode,
            "max_output_tokens": spec.max_output_tokens,
            "context_contract_bytes": spec.context_contract_bytes,
            "transport": spec.transport,
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
            "turn_timeout_seconds": spec.turn_timeout_seconds,
            "runtime_profile_key": runtime_profile_key,
            "runtime_state_dir": str(profile_dir / "provider-state"),
            "credential_stdin": bool(credential),
            "provider_endpoint": provider_endpoint,
            "provider_server_pid": provider_server_pid,
            "resume_required": bool(
                session.get("recovery_required") or int(session.get("turn_count") or 0) > 0
            ),
        }
        CanonicalBridgeLaunchConfig.parse_strict(config)
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        stdout_path.touch(exist_ok=True)
        stderr_path.touch(exist_ok=True)
        for path in (config_path, stdout_path, stderr_path):
            try:
                path.chmod(0o600)
            except OSError:
                pass
        env = sanitized_child_environment(
            {
                "AGENTSASSEMBLE_BRIDGE_SERVER_URL": server_url,
                "AGENTSASSEMBLE_BRIDGE_TICKET": ticket,
                "AGENTSASSEMBLE_BRIDGE_CONFIG": str(config_path),
                "PYTHONUNBUFFERED": "1",
            }
        )
        package_root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = os.pathsep.join(part for part in (package_root, env.get("PYTHONPATH", "")) if part)
        command = [
            sys.executable,
            "-m",
            "agentsassemble.application.agent_bridge_entrypoint",
        ]
        secure_launch_required = bool(credential or session_token)
        secure_launch_payload = (
            encode_secure_launch_payload(
                {
                    "credential": credential,
                    "session_token": session_token,
                }
            )
            if secure_launch_required
            else b""
        )
        process = self._popen_factory(
            command,
            stdin=subprocess.PIPE if secure_launch_required else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            cwd=package_root,
            env=env,
            start_new_session=True,
        )
        if secure_launch_required:
            stream = getattr(process, "stdin", None)
            if stream is None:
                process.terminate()
                raise RuntimeError("Agent Bridge did not expose secure launch stdin.")
            try:
                stream.write(secure_launch_payload)
                stream.flush()
            finally:
                stream.close()
        handle = _BridgeHandle(
            handle_id=bridge_launch_id,
            room_id=room_id,
            session_id=session_id,
            runtime_profile_key=runtime_profile_key,
            resolved_executable=executable,
            process=process,
            config_path=config_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            provider_process_shared=spec.normalized_provider_kind() == "opencode_server",
            sensitive_values=tuple(
                value
                for value in (credential, ticket, session_token)
                if value
            ),
        )
        with self._lock:
            self._handles[key] = handle
        self._sensitive_values.register(
            room_id,
            session_id,
            handle.handle_id,
            handle.sensitive_values,
        )
        handle.stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(handle,),
            name=f"AgentsAssembleBridgeStderr-{spec.agent_id}",
            daemon=True,
        )
        handle.stderr_thread.start()
        threading.Thread(
            target=self._watch,
            args=(handle,),
            name=f"AgentsAssembleBridgeWatch-{spec.agent_id}",
            daemon=True,
        ).start()
        return self._launch_payload(handle, spec, runtime_reused=False, resolved_executable=executable)

    @contextmanager
    def _own_bridge_launch(self, key: tuple[str, str]) -> Iterator[None]:
        with self._lock:
            ownership = self._launch_ownership.get(key)
            if ownership is None:
                ownership = _BridgeLaunchOwnership()
                self._launch_ownership[key] = ownership
            ownership.users += 1
        ownership.lock.acquire()
        try:
            yield
        finally:
            ownership.lock.release()
            with self._lock:
                ownership.users -= 1
                if ownership.users == 0 and self._launch_ownership.get(key) is ownership:
                    self._launch_ownership.pop(key, None)

    def stop(
        self,
        room_id: str,
        session_id: str,
        *,
        timeout_seconds: float = 2.0,
        handle_id: str = "",
    ) -> dict[str, object]:
        key = (room_id, session_id)
        with self._lock:
            handle = self._handles.get(key)
            if handle is None:
                self._sensitive_values.release_session(room_id, session_id)
                return {"stopped": False, "alive": False, "bridge_pid": None, "reason": "handle_not_found"}
            if not handle_id or handle.handle_id != handle_id:
                return {
                    "stopped": False,
                    "alive": handle.process.poll() is None,
                    "bridge_pid": handle.process.pid,
                    "reason": "handle_mismatch",
                }
            handle.stopping = True
        process = handle.process
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=max(0.1, float(timeout_seconds)))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
        self._finish_stderr(handle)
        with self._lock:
            self._handles.pop(key, None)
        self._sensitive_values.release_registration(
            room_id,
            session_id,
            handle.handle_id,
        )
        return {
            "stopped": True,
            "alive": process.poll() is None,
            "bridge_pid": process.pid,
            "provider_process_shared": handle.provider_process_shared,
        }

    def close(self) -> CleanupReport:
        report = CleanupReport("native_cli_bridge_process_manager")
        with self._lock:
            keys = list(self._handles)
        for room_id, session_id in keys:
            with self._lock:
                handle = self._handles.get((room_id, session_id))
            if handle is None:
                report.record_success()
                continue
            try:
                result = self.stop(room_id, session_id, handle_id=handle.handle_id)
                if result.get("alive"):
                    raise RuntimeError("Owned Agent Bridge remained alive after stop.")
                report.record_success()
            except Exception as error:
                report.record_failure(
                    "bridge.stop",
                    error,
                    handle_id=handle.handle_id,
                    orphaned=handle.process.poll() is None,
                )
        with self._lock:
            opencode = self._opencode_server
            self._opencode_server = None
        if opencode is not None:
            try:
                opencode.stop()
                process = opencode.process
                if process is not None and process.poll() is None:
                    raise RuntimeError("Owned OpenCode server remained alive after stop.")
                report.record_success()
            except Exception as error:
                process = opencode.process
                report.record_failure(
                    "opencode_server.stop",
                    error,
                    handle_id="shared-opencode-server",
                    orphaned=process is not None and process.poll() is None,
                )
        if not report.orphaned_handle_ids:
            self._sensitive_values.clear()
        self.last_cleanup_report = report
        return report

    def health(self, room_id: str, session_id: str) -> dict[str, object]:
        with self._lock:
            handle = self._handles.get((room_id, session_id))
        if handle is None:
            return {"running": False, "bridge_pid": None}
        return {
            "running": handle.process.poll() is None,
            "bridge_pid": handle.process.pid,
            "bridge_handle_id": handle.handle_id,
            "returncode": handle.process.poll(),
            "runtime_profile_key": handle.runtime_profile_key,
            **self._stderr_snapshot(handle),
        }

    def room_portal_publication(
        self,
        room_id: str,
        session_id: str,
        turn_id: str,
        *,
        handle_id: str = "",
    ) -> dict[str, object] | None:
        """Read publication content only from the active server-owned portal."""

        with self._lock:
            handle = self._handles.get((room_id, session_id))
            if (
                handle is None
                or handle.handle_id != handle_id
                or handle.process.poll() is not None
            ):
                return None
            profile_root = handle.config_path.parent.resolve()
            portal_root = (profile_root / "provider-state" / "room-portal").resolve()
            try:
                portal_root.relative_to(profile_root)
            except ValueError:
                return None
            portal = RoomPortal(portal_root, participant_id=session_id)
            publication = portal.publication_result(turn_id)
        if not publication.has_message:
            return None
        return {
            "content": publication.content,
            "target_agent_id": publication.target_agent_id,
            "kind": publication.message_kind,
            "vote_id": publication.vote_id,
            "vote_question": publication.vote_question,
            "vote_options": list(publication.vote_options),
            "vote_duration_seconds": publication.vote_duration_seconds,
            "vote_choice": publication.vote_choice,
        }

    def redact_diagnostic(
        self,
        room_id: str,
        session_id: str,
        value: object,
        *,
        limit: int = _PERSISTED_STDERR_LIMIT,
    ) -> str:
        """Redact credentials retained by active launches of one Agent Session."""

        return self._sensitive_values.redact_diagnostic(
            room_id,
            session_id,
            value,
            limit=limit,
        )

    def redact_stream_delta(
        self,
        room_id: str,
        session_id: str,
        turn_id: str,
        value: object,
    ) -> str:
        return self._sensitive_values.redact_stream_delta(
            room_id,
            session_id,
            turn_id,
            value,
        )

    def redact_public_payload(self, room_id, session_id, value):
        return self._sensitive_values.redact_public_payload(room_id, session_id, value)

    def discard_stream_delta(self, room_id: str, session_id: str, turn_id: str) -> None:
        self._sensitive_values.discard_stream_delta(room_id, session_id, turn_id)

    def _watch(self, handle: _BridgeHandle) -> None:
        returncode = handle.process.wait()
        self._finish_stderr(handle)
        key = (handle.room_id, handle.session_id)
        with self._lock:
            current = self._handles.get(key)
            is_current = current is handle
            if is_current:
                self._handles.pop(key, None)
            listener = self._on_exit if is_current else None
            stopping = handle.stopping
        if listener is not None and not stopping:
            try:
                listener(
                    handle.room_id,
                    handle.session_id,
                    int(returncode),
                    str(self._stderr_snapshot(handle)["stderr_tail"]),
                )
            finally:
                self._sensitive_values.release_registration(
                    handle.room_id,
                    handle.session_id,
                    handle.handle_id,
                )
        else:
            self._sensitive_values.release_registration(
                handle.room_id,
                handle.session_id,
                handle.handle_id,
            )

    def _drain_stderr(self, handle: _BridgeHandle) -> None:
        stream = getattr(handle.process, "stderr", None)
        if stream is None:
            return
        try:
            try:
                for line in stream:
                    data = line.encode("utf-8", errors="replace") if isinstance(line, str) else bytes(line)
                    with handle.stderr_lock:
                        handle.stderr_byte_count += len(data)
                        handle.stderr_line_count += 1
                        if b"warn" in data.lower() or b"warning" in data.lower():
                            handle.stderr_warning_count += 1
                        handle.stderr_tail.extend(data)
                        raw_tail_limit = _raw_stderr_tail_limit(handle)
                        if len(handle.stderr_tail) > raw_tail_limit:
                            del handle.stderr_tail[:-raw_tail_limit]
                            handle.stderr_tail_truncated = True
            except (OSError, ValueError):
                pass
        finally:
            self._persist_stderr_snapshot(handle)

    def _finish_stderr(self, handle: _BridgeHandle) -> None:
        thread = handle.stderr_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        stream = getattr(handle.process, "stderr", None)
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
        self._persist_stderr_snapshot(handle)

    @staticmethod
    def _stderr_snapshot(handle: _BridgeHandle) -> dict[str, object]:
        with handle.stderr_lock:
            tail = redact_persisted_diagnostic_bytes(
                handle.stderr_tail,
                limit=_PERSISTED_STDERR_LIMIT,
                exact_values=handle.sensitive_values,
            )
            return {
                "stderr_drained": True,
                "stderr_byte_count": handle.stderr_byte_count,
                "stderr_line_count": handle.stderr_line_count,
                "stderr_warning_count": handle.stderr_warning_count,
                "stderr_tail_truncated": handle.stderr_tail_truncated,
                "stderr_tail": tail.decode("utf-8"),
            }

    def _persist_stderr_snapshot(self, handle: _BridgeHandle) -> None:
        with handle.stderr_lock:
            persisted = redact_persisted_diagnostic_bytes(
                handle.stderr_tail,
                limit=_PERSISTED_STDERR_LIMIT,
                exact_values=handle.sensitive_values,
            )
        try:
            handle.stderr_path.write_bytes(persisted)
            handle.stderr_path.chmod(0o600)
        except OSError:
            pass

    def _resolve(self, executable: str) -> str:
        if not executable:
            return ""
        path = Path(executable).expanduser()
        if path.is_absolute():
            return str(path) if path.is_file() else ""
        return str(self._resolve_executable(executable) or "")

    def _ensure_opencode_server(self, executable: str) -> OpenCodeServerProcess:
        with self._lock:
            current = self._opencode_server
            if current is not None and current.process is not None and current.process.poll() is None:
                return current
            handle = OpenCodeServerProcess(
                cwd=self.output_root,
                executable=executable,
                popen_factory=self._popen_factory,
            )
            self._opencode_server = handle
        handle.start()
        return handle

    @staticmethod
    def _launch_payload(
        handle: _BridgeHandle,
        spec: NativeCliProviderSpec,
        *,
        runtime_reused: bool,
        resolved_executable: str = "",
    ) -> dict[str, object]:
        return {
            "bridge_handle_id": handle.handle_id,
            "bridge_pid": handle.process.pid,
            "runtime_reused": runtime_reused,
            "runtime_profile_key": handle.runtime_profile_key,
            "resolved_executable": resolved_executable or handle.resolved_executable,
            "command_configured": list(spec.command),
            "config_path": str(handle.config_path),
            "stdout_path": str(handle.stdout_path),
            "stderr_path": str(handle.stderr_path),
        }
