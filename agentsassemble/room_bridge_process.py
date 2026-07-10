from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable

from agentsassemble.native_cli_providers import NativeCliProviderSpec, validate_native_cli_provider_spec


BridgeExitListener = Callable[[str, str, int, str], None]


@dataclass
class _BridgeHandle:
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


class NativeCliBridgeProcessManager:
    """Launches server-owned Agent Bridge processes without exposing tickets."""

    def __init__(
        self,
        output_root: str | Path,
        *,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        executable_resolver: Callable[[str], str | None] = shutil.which,
        on_exit: BridgeExitListener | None = None,
    ) -> None:
        self.output_root = Path(output_root)
        self._popen_factory = popen_factory
        self._resolve_executable = executable_resolver
        self._on_exit = on_exit
        self._lock = threading.RLock()
        self._handles: dict[tuple[str, str], _BridgeHandle] = {}

    def set_exit_listener(self, listener: BridgeExitListener | None) -> None:
        with self._lock:
            self._on_exit = listener

    def start(
        self,
        room_id: str,
        session: dict[str, object],
        spec: NativeCliProviderSpec,
        *,
        server_url: str = "",
        ticket_issuer: Callable[[dict[str, object]], str] | None = None,
    ) -> dict[str, object]:
        validate_native_cli_provider_spec(spec)
        session_id = str(session.get("session_id") or spec.agent_id)
        runtime_profile_key = spec.runtime_profile_key()
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
        executable = self._resolve(spec.command[0] if spec.command else "")
        if not executable:
            raise FileNotFoundError(f"configured command missing: {spec.command[0] if spec.command else ''}")
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
            "operator": False,
        }
        ticket = str(ticket_issuer(identity) or "")
        if not ticket:
            raise ValueError("Agent Bridge ticket issuer returned an empty ticket.")
        bridge_dir = self.output_root / "rooms" / room_id / "bridges" / session_id
        profile_dir = bridge_dir / runtime_profile_key
        profile_dir.mkdir(parents=True, exist_ok=True)
        config_path = profile_dir / "config.json"
        stdout_path = profile_dir / "stdout.log"
        stderr_path = profile_dir / "stderr.log"
        config = {
            "room_id": room_id,
            "participant_id": spec.agent_id,
            "session_id": session_id,
            "provider_kind": spec.normalized_provider_kind(),
            "command": list(spec.command),
            "cwd": spec.cwd,
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
            "turn_timeout_seconds": spec.turn_timeout_seconds,
            "runtime_profile_key": runtime_profile_key,
            "runtime_state_dir": str(profile_dir / "provider-state"),
        }
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        stdout_path.touch(exist_ok=True)
        stderr_path.touch(exist_ok=True)
        for path in (config_path, stdout_path, stderr_path):
            try:
                path.chmod(0o600)
            except OSError:
                pass
        env = os.environ.copy()
        env.update(
            {
                "AGENTSASSEMBLE_BRIDGE_SERVER_URL": server_url,
                "AGENTSASSEMBLE_BRIDGE_TICKET": ticket,
                "AGENTSASSEMBLE_BRIDGE_CONFIG": str(config_path),
                "PYTHONUNBUFFERED": "1",
            }
        )
        package_root = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = os.pathsep.join(part for part in (package_root, env.get("PYTHONPATH", "")) if part)
        command = [sys.executable, "-m", "agentsassemble.room_agent_bridge"]
        process = self._popen_factory(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            cwd=package_root,
            env=env,
            start_new_session=True,
        )
        handle = _BridgeHandle(
            room_id=room_id,
            session_id=session_id,
            runtime_profile_key=runtime_profile_key,
            resolved_executable=executable,
            process=process,
            config_path=config_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        with self._lock:
            self._handles[key] = handle
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

    def stop(
        self,
        room_id: str,
        session_id: str,
        *,
        timeout_seconds: float = 2.0,
        provider_pid: int | None = None,
    ) -> dict[str, object]:
        key = (room_id, session_id)
        with self._lock:
            handle = self._handles.get(key)
            if handle is None:
                provider_alive = _terminate_provider_process(provider_pid, timeout_seconds=timeout_seconds)
                return {"stopped": True, "alive": provider_alive, "bridge_pid": None, "provider_alive": provider_alive}
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
        provider_alive = _terminate_provider_process(provider_pid, timeout_seconds=timeout_seconds)
        return {
            "stopped": True,
            "alive": process.poll() is None or provider_alive,
            "bridge_pid": process.pid,
            "provider_alive": provider_alive,
        }

    def close(self) -> None:
        with self._lock:
            keys = list(self._handles)
        for room_id, session_id in keys:
            try:
                self.stop(room_id, session_id)
            except Exception:
                continue

    def health(self, room_id: str, session_id: str) -> dict[str, object]:
        with self._lock:
            handle = self._handles.get((room_id, session_id))
        if handle is None:
            return {"running": False, "bridge_pid": None}
        return {
            "running": handle.process.poll() is None,
            "bridge_pid": handle.process.pid,
            "returncode": handle.process.poll(),
            "runtime_profile_key": handle.runtime_profile_key,
            **self._stderr_snapshot(handle),
        }

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
            listener(
                handle.room_id,
                handle.session_id,
                int(returncode),
                str(self._stderr_snapshot(handle)["stderr_tail"]),
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
                        if len(handle.stderr_tail) > 16_000:
                            del handle.stderr_tail[:-16_000]
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
            return {
                "stderr_drained": True,
                "stderr_byte_count": handle.stderr_byte_count,
                "stderr_line_count": handle.stderr_line_count,
                "stderr_warning_count": handle.stderr_warning_count,
                "stderr_tail_truncated": handle.stderr_tail_truncated,
                "stderr_tail": bytes(handle.stderr_tail).decode("utf-8", errors="replace"),
            }

    def _persist_stderr_snapshot(self, handle: _BridgeHandle) -> None:
        with handle.stderr_lock:
            tail = bytes(handle.stderr_tail)
        try:
            handle.stderr_path.write_bytes(tail)
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

    @staticmethod
    def _launch_payload(
        handle: _BridgeHandle,
        spec: NativeCliProviderSpec,
        *,
        runtime_reused: bool,
        resolved_executable: str = "",
    ) -> dict[str, object]:
        return {
            "bridge_pid": handle.process.pid,
            "runtime_reused": runtime_reused,
            "runtime_profile_key": handle.runtime_profile_key,
            "resolved_executable": resolved_executable or handle.resolved_executable,
            "command_configured": list(spec.command),
            "config_path": str(handle.config_path),
            "stdout_path": str(handle.stdout_path),
            "stderr_path": str(handle.stderr_path),
        }

def _terminate_provider_process(provider_pid: int | None, *, timeout_seconds: float) -> bool:
    try:
        pid = int(provider_pid or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0 or not hasattr(os, "killpg"):
        return False
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    while time.monotonic() < deadline:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return False
        time.sleep(0.02)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return False
    return _process_group_alive(pid)


def _process_group_alive(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
