from __future__ import annotations

from dataclasses import dataclass
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

from agentsassemble.room_realtime import NativeCliProviderSpec, validate_native_cli_provider_spec


BridgeExitListener = Callable[[str, str, int, str], None]


@dataclass
class _BridgeHandle:
    room_id: str
    session_id: str
    process: subprocess.Popen[bytes]
    config_path: Path
    stdout_path: Path
    stderr_path: Path
    stopping: bool = False


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
        key = (room_id, session_id)
        with self._lock:
            existing = self._handles.get(key)
            if existing is not None and existing.process.poll() is None:
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
        bridge_dir.mkdir(parents=True, exist_ok=True)
        config_path = bridge_dir / "config.json"
        stdout_path = bridge_dir / "stdout.log"
        stderr_path = bridge_dir / "stderr.log"
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
        }
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
        with stdout_path.open("ab") as stdout_file, stderr_path.open("ab") as stderr_file:
            process = self._popen_factory(
                command,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                cwd=package_root,
                env=env,
                start_new_session=True,
            )
        handle = _BridgeHandle(
            room_id=room_id,
            session_id=session_id,
            process=process,
            config_path=config_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        with self._lock:
            self._handles[key] = handle
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
            "stderr_tail": _tail(handle.stderr_path),
        }

    def _watch(self, handle: _BridgeHandle) -> None:
        returncode = handle.process.wait()
        key = (handle.room_id, handle.session_id)
        with self._lock:
            current = self._handles.get(key)
            if current is handle:
                self._handles.pop(key, None)
            listener = self._on_exit
            stopping = handle.stopping
        if listener is not None and not stopping:
            listener(handle.room_id, handle.session_id, int(returncode), _tail(handle.stderr_path))

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
            "resolved_executable": resolved_executable,
            "command_configured": list(spec.command),
            "config_path": str(handle.config_path),
            "stdout_path": str(handle.stdout_path),
            "stderr_path": str(handle.stderr_path),
        }


def _tail(path: Path, *, max_chars: int = 16000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-max(1, int(max_chars)) :]


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
