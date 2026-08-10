"""Isolated subprocess host for first-party plugin server entrypoints."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from agentsassemble.plugin.manifest import PluginManifest
from agentsassemble.providers.process_environment import sanitized_child_environment
from agentsassemble.room.text import clean_room_text

EventHandler = Callable[[dict[str, object]], None]


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
        self._lock = threading.RLock()
        self._command_id = 0
        self._last_error = ""

    def start(self) -> dict[str, object]:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return self.health()
            entry = (self.manifest.root / self.manifest.server_entry).resolve()
            env = sanitized_child_environment(
                {
                    "AGENTSASSEMBLE_PLUGIN_ID": self.manifest.id,
                    "AGENTSASSEMBLE_PLUGIN_ROOM_ID": self.room_id,
                    "AGENTSASSEMBLE_PLUGIN_STORAGE": str(self.storage_dir),
                    "PYTHONPATH": os.pathsep.join(
                        part
                        for part in (
                            str(self.manifest.root),
                            str(Path(__file__).resolve().parents[2]),
                            os.environ.get("PYTHONPATH", ""),
                        )
                        if part
                    ),
                }
            )
            process = self._popen_factory(
                [sys.executable, "-u", str(entry)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=str(self.manifest.root),
                env=env,
                start_new_session=True,
            )
            self._process = process
            self._reader = threading.Thread(
                target=self._read_stdout,
                name=f"plugin-{self.manifest.id}-{self.room_id}",
                daemon=True,
            )
            self._reader.start()
            self._last_error = ""
            self.send_command(
                {
                    "type": "plugin.start",
                    "room_id": self.room_id,
                    "plugin_id": self.manifest.id,
                    "storage_dir": str(self.storage_dir),
                }
            )
            return self.health()

    def send_command(self, payload: dict[str, object]) -> str:
        with self._lock:
            process = self._process
            if process is None or process.stdin is None or process.poll() is not None:
                raise RuntimeError("Plugin process is not running.")
            self._command_id += 1
            command_id = f"cmd-{self._command_id}"
            message = {"id": command_id, **payload}
            process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            process.stdin.flush()
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
                continue
            if not isinstance(event, dict):
                continue
            if str(event.get("type") or "") == "plugin.error":
                self._last_error = str(event.get("message") or "plugin error")
            if self._on_event is not None:
                try:
                    self._on_event(event)
                except Exception:
                    pass
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


__all__ = ["PluginProcessHost"]
