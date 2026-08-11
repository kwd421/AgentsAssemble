"""Isolated subprocess host for first-party plugin server entrypoints."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from agentsassemble.plugin.manifest import PluginManifest
from agentsassemble.providers.process_environment import sanitized_child_environment
from agentsassemble.room.text import clean_room_text

try:
    import resource
except ImportError:  # Windows does not expose POSIX resource limits.
    resource = None

EventHandler = Callable[[dict[str, object]], None]


class PluginProcessCommandError(RuntimeError):
    """The isolated plugin process explicitly rejected one command."""

    def __init__(self, message: str, *, code: str, command_id: str) -> None:
        super().__init__(message)
        self.code = clean_room_text(code, limit=96) or "plugin_command_failed"
        self.command_id = clean_room_text(command_id, limit=64)


@dataclass
class _PendingPluginCommand:
    event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, object] | None = None


class PluginProcessHost:
    """One plugin process per room. Communicates over JSONL stdin/stdout."""

    def __init__(
        self,
        *,
        manifest: PluginManifest,
        room_id: str,
        storage_dir: Path,
        on_event: EventHandler | None = None,
        popen_factory=subprocess.Popen,
    ) -> None:
        self.manifest = manifest
        self.room_id = clean_room_text(room_id, limit=128)
        self.storage_dir = Path(storage_dir)
        self._on_event = on_event
        self._popen_factory = popen_factory
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._lock = threading.RLock()
        self._command_id = 0
        self._last_error = ""
        self._pending_commands: dict[str, _PendingPluginCommand] = {}

    def start(self, *, initial_state: dict[str, object] | None = None) -> dict[str, object]:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return self.health()
            entry = (self.manifest.root / self.manifest.server_entry).resolve()
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            env = sanitized_child_environment(
                {
                    "AGENTSASSEMBLE_PLUGIN_ID": self.manifest.id,
                    "AGENTSASSEMBLE_PLUGIN_ROOM_ID": self.room_id,
                    "AGENTSASSEMBLE_PLUGIN_STORAGE": str(self.storage_dir),
                    "HOME": str(self.storage_dir),
                    "TMPDIR": str(self.storage_dir),
                }
            )
            runner = Path(__file__).with_name("isolated_runner.py").resolve()
            process_options: dict[str, object] = {}
            if os.name == "posix":
                process_options["preexec_fn"] = _limit_plugin_process
            process = self._popen_factory(
                [
                    sys.executable,
                    "-I",
                    "-u",
                    str(runner),
                    "--plugin-root",
                    str(self.manifest.root),
                    "--entry",
                    str(entry),
                    "--storage",
                    str(self.storage_dir),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=str(self.manifest.root),
                env=env,
                start_new_session=True,
                **process_options,
            )
            self._process = process
            self._reader = threading.Thread(
                target=self._read_stdout,
                name=f"plugin-{self.manifest.id}-{self.room_id}",
                daemon=True,
            )
            self._reader.start()
            self._stderr_reader = threading.Thread(
                target=self._read_stderr,
                name=f"plugin-{self.manifest.id}-{self.room_id}-stderr",
                daemon=True,
            )
            self._stderr_reader.start()
            self._last_error = ""
            self.send_command(
                {
                    "type": "plugin.start",
                    "room_id": self.room_id,
                    "plugin_id": self.manifest.id,
                    "storage_dir": str(self.storage_dir),
                    "initial_state": dict(initial_state or {}),
                }
            )
            return self.health()

    def send_command(self, payload: dict[str, object]) -> str:
        return self._send_command(payload)

    def send_command_and_wait(
        self,
        payload: dict[str, object],
        *,
        timeout_seconds: float = 5.0,
    ) -> tuple[str, dict[str, object]]:
        pending = _PendingPluginCommand()
        command_id = self._send_command(payload, pending=pending)
        try:
            if not pending.event.wait(max(0.1, float(timeout_seconds))):
                raise TimeoutError(
                    f"Plugin command {command_id} timed out waiting for a process result."
                )
            result = pending.result
            if not isinstance(result, dict):
                raise RuntimeError(
                    f"Plugin command {command_id} completed without a structured result."
                )
            if clean_room_text(result.get("type"), limit=64) == "plugin.error":
                raise PluginProcessCommandError(
                    clean_room_text(result.get("message"), limit=2000)
                    or "Plugin command failed.",
                    code=clean_room_text(result.get("code"), limit=96),
                    command_id=command_id,
                )
            return command_id, result
        finally:
            with self._lock:
                self._pending_commands.pop(command_id, None)

    def _send_command(
        self,
        payload: dict[str, object],
        *,
        pending: _PendingPluginCommand | None = None,
    ) -> str:
        with self._lock:
            process = self._process
            if process is None or process.stdin is None or process.poll() is not None:
                raise RuntimeError("Plugin process is not running.")
            self._command_id += 1
            command_id = f"cmd-{self._command_id}"
            if pending is not None:
                self._pending_commands[command_id] = pending
            message = {"id": command_id, **payload}
            try:
                process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                process.stdin.flush()
            except Exception:
                self._pending_commands.pop(command_id, None)
                raise
            return command_id

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.write(json.dumps({"type": "plugin.stop"}) + "\n")
                process.stdin.flush()
                process.stdin.close()
        except Exception:
            pass
        process.terminate()
        try:
            process.wait(timeout=max(0.1, timeout_seconds))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        for reader in (self._reader, self._stderr_reader):
            if reader is not None and reader is not threading.current_thread():
                reader.join(timeout=0.5)
        self._reader = None
        self._stderr_reader = None

    def health(self) -> dict[str, object]:
        process = self._process
        return {
            "plugin_id": self.manifest.id,
            "room_id": self.room_id,
            "running": bool(process is not None and process.poll() is None),
            "pid": process.pid if process is not None else None,
            "last_error": self._last_error,
        }

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for raw_line in process.stdout:
            line = raw_line[:-1] if raw_line.endswith("\n") else raw_line
            if line.endswith("\r"):
                line = line[:-1]
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                self._emit_host_error("invalid_plugin_output", "Plugin emitted invalid JSON.")
                continue
            if not isinstance(event, dict):
                continue
            if str(event.get("type") or "") == "plugin.error":
                self._last_error = str(event.get("message") or "plugin error")
            self._resolve_pending_command(event)
            if self._on_event is not None:
                try:
                    self._on_event(event)
                except Exception as error:
                    self._last_error = f"Plugin event callback failed: {error}"
        if process.poll() is not None and self._on_event is not None:
            self._on_event(
                {
                    "type": "plugin.error",
                    "code": "plugin_process_exited",
                    "message": "Plugin process exited.",
                    "plugin_id": self.manifest.id,
                    "room_id": self.room_id,
                }
            )

    def _resolve_pending_command(self, event: dict[str, object]) -> None:
        command_id = clean_room_text(
            event.get("command_id") or event.get("id"),
            limit=64,
        )
        if not command_id:
            return
        with self._lock:
            pending = self._pending_commands.get(command_id)
            if pending is None:
                return
            pending.result = dict(event)
            pending.event.set()

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        tail = ""
        for raw_line in process.stderr:
            tail = (tail + raw_line)[-2000:]
        if tail.strip():
            self._emit_host_error("plugin_stderr", tail.strip())

    def _emit_host_error(self, code: str, message: str) -> None:
        self._last_error = str(message)[:2000]
        if self._on_event is not None:
            self._on_event(
                {
                    "type": "plugin.error",
                    "code": code,
                    "message": self._last_error,
                    "plugin_id": self.manifest.id,
                    "room_id": self.room_id,
                }
            )


def _limit_plugin_process() -> None:
    if resource is None:
        return
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))
    if hasattr(resource, "RLIMIT_AS"):
        try:
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        except (OSError, ValueError):
            pass


__all__ = ["PluginProcessCommandError", "PluginProcessHost"]
