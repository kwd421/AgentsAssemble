from __future__ import annotations

import json
import os
import re
import signal
import shutil
import subprocess
import threading
import time
import fcntl
import struct
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from agentsassemble.live_cli_output import extract_live_cli_terminal_message, strip_terminal_ansi
from agentsassemble.live_cli_transcripts import (
    LiveCliMessageExtractionError,
    LiveCliMessageSource,
    LiveCliMessageSnapshot,
    make_live_cli_message_source,
)
from agentsassemble.meeting_events import clean_lobby_text

try:
    import pty
except ImportError:  # pragma: no cover - host dependent
    pty = None  # type: ignore[assignment]

try:
    import termios
except ImportError:  # pragma: no cover - host dependent
    termios = None  # type: ignore[assignment]

try:
    import select
except ImportError:  # pragma: no cover - host dependent
    select = None  # type: ignore[assignment]


GENERAL_ROOM_ID = "general"
LIVE_CLI_EVENT_KINDS = {
    "user_message",
    "agent_input",
    "agent_delta",
    "agent_message",
    "agent_error",
    "system",
}


class AgentRuntime(Protocol):
    last_seen_event_id: str

    def start(self) -> dict[str, object]:
        ...

    def deliver(self, events: list[dict[str, object]]) -> None:
        ...

    def read_output(
        self,
        *,
        timeout_seconds: float,
        on_delta: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        ...

    def interrupt(self) -> None:
        ...

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        ...

    def health(self) -> dict[str, object]:
        ...


class GeneralRoomEventStore:
    """Append-only #general event log for the CLI-first MVP."""

    def __init__(self, output_root: str | Path, *, room_id: str = GENERAL_ROOM_ID) -> None:
        clean_room_id = clean_lobby_text(room_id, limit=128) or GENERAL_ROOM_ID
        if clean_room_id in {".", ".."} or "/" in clean_room_id or "\\" in clean_room_id:
            raise ValueError("room_id is invalid.")
        self.output_root = Path(output_root)
        self.room_id = clean_room_id
        self._lock = threading.RLock()
        self._listeners: list[Callable[[dict[str, object]], None]] = []
        self.events_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def events_path(self) -> Path:
        return self.output_root / "rooms" / self.room_id / "events.jsonl"

    def append_user_message(
        self,
        actor_id: str,
        content: str,
        *,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return self.append_event("user_message", actor_id=actor_id, actor_type="user", content=content, metadata=metadata)

    def append_agent_input(
        self,
        agent_id: str,
        content: str,
        *,
        source_event_id: str,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return self.append_event(
            "agent_input",
            actor_id=agent_id,
            actor_type="agent",
            content=content,
            metadata={"source_event_id": source_event_id, **dict(metadata or {})},
        )

    def append_agent_delta(
        self,
        agent_id: str,
        content: str,
        *,
        source_event_id: str,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return self.append_event(
            "agent_delta",
            actor_id=agent_id,
            actor_type="agent",
            content=content,
            metadata={"source_event_id": source_event_id, **dict(metadata or {})},
        )

    def append_agent_message(
        self,
        agent_id: str,
        content: str,
        *,
        source_event_id: str,
        relay_depth: int = 0,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return self.append_event(
            "agent_message",
            actor_id=agent_id,
            actor_type="agent",
            content=content,
            metadata={
                "source_event_id": source_event_id,
                "relay_depth": max(0, int(relay_depth or 0)),
                **dict(metadata or {}),
            },
        )

    def append_agent_error(
        self,
        agent_id: str,
        message: str,
        *,
        source_event_id: str = "",
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return self.append_event(
            "agent_error",
            actor_id=agent_id,
            actor_type="agent",
            content=message,
            metadata={"source_event_id": source_event_id, **dict(metadata or {})},
        )

    def append_system(self, message: str) -> dict[str, object]:
        return self.append_event("system", actor_id="system", actor_type="system", content=message)

    def add_listener(self, listener: Callable[[dict[str, object]], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def append_event(
        self,
        kind: str,
        *,
        actor_id: str,
        actor_type: str,
        content: str,
        metadata: dict[str, object] | None = None,
        **extra: object,
    ) -> dict[str, object]:
        clean_kind = clean_lobby_text(kind, limit=64)
        if clean_kind not in LIVE_CLI_EVENT_KINDS:
            raise ValueError(f"Unsupported #general event kind: {kind}")
        event = {
            "event_id": uuid4().hex[:12],
            "created_at": datetime.now(UTC).isoformat(),
            "room_id": self.room_id,
            "actor_id": clean_lobby_text(actor_id, limit=128),
            "actor_type": clean_lobby_text(actor_type, limit=32),
            "kind": clean_kind,
            "content": clean_lobby_text(content, limit=12000),
            "metadata": _clean_metadata({**dict(metadata or {}), **extra}),
        }
        with self._lock:
            with self.events_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(dict(event))
            except Exception:
                continue
        return event

    def read_events(self, *, after: str = "") -> list[dict[str, object]]:
        if not self.events_path.exists():
            return []
        events: list[dict[str, object]] = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        clean_after = clean_lobby_text(after, limit=128)
        if not clean_after:
            return events
        for index, event in enumerate(events):
            if str(event.get("event_id") or "") == clean_after:
                return events[index + 1 :]
        return events

    def latest_event_id(self) -> str:
        events = self.read_events()
        if not events:
            return ""
        return clean_lobby_text(events[-1].get("event_id"), limit=128)


class LiveCliRuntime:
    """Persistent PTY-backed runtime for one local interactive CLI."""

    def __init__(
        self,
        agent_id: str,
        command: list[str],
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        idle_quiet_seconds: float = 0.35,
        submit_newline: str = "\n",
        input_mode: str = "line",
        terminal_rows: int = 40,
        terminal_columns: int = 120,
        startup_quiet_seconds: float = 0.0,
        startup_timeout_seconds: float = 0.0,
        max_output_bytes: int = 256_000,
        message_source: LiveCliMessageSource | None = None,
    ) -> None:
        if not command:
            raise ValueError("Live CLI command is required.")
        self.agent_id = clean_lobby_text(agent_id, limit=128)
        if not self.agent_id:
            raise ValueError("agent_id is required.")
        self.command = list(command)
        self.cwd = Path(cwd).expanduser().resolve() if cwd else Path.cwd().resolve()
        self.env = dict(env or {})
        self._popen_factory = popen_factory
        self.idle_quiet_seconds = max(0.01, float(idle_quiet_seconds))
        self.submit_newline = submit_newline or "\n"
        self.input_mode = clean_lobby_text(input_mode, limit=64) or "line"
        if self.input_mode not in {"line", "bracketed_paste"}:
            raise ValueError("input_mode must be line or bracketed_paste.")
        self.terminal_rows = max(10, int(terminal_rows or 40))
        self.terminal_columns = max(40, int(terminal_columns or 120))
        self.startup_quiet_seconds = max(0.0, float(startup_quiet_seconds or 0.0))
        self.startup_timeout_seconds = max(0.0, float(startup_timeout_seconds or 0.0))
        self.max_output_bytes = max(1, int(max_output_bytes))
        self._message_source = message_source or make_live_cli_message_source(
            self.agent_id,
            self.command,
            cwd=self.cwd,
        )
        self._message_turn_started = False
        self.last_seen_event_id = ""
        self._lock = threading.RLock()
        self._master_fd: int | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self._last_error = ""
        self._started_at = ""
        self._stopped_at = ""
        self._resolved_executable = ""
        self._startup_drained = False

    def start(self) -> dict[str, object]:
        with self._lock:
            if self._is_running_locked():
                return self.health()
            if not live_cli_supported():
                raise RuntimeError("PTY live CLI sessions are not available on this host.")
            self._resolved_executable = _resolve_executable(self.command)
            if not self._resolved_executable:
                self._last_error = "configured command missing"
                raise FileNotFoundError(f"configured command missing: {self.command[0]}")
            assert pty is not None
            self._message_source.prepare_start()
            self._message_source.begin_turn()
            self._message_turn_started = True
            master_fd, slave_fd = pty.openpty()
            try:
                _configure_slave_terminal(slave_fd, rows=self.terminal_rows, columns=self.terminal_columns)
                process_env = os.environ.copy()
                process_env.update(self.env)
                if not process_env.get("TERM") or process_env.get("TERM") == "dumb":
                    process_env["TERM"] = "xterm-256color"
                process_env.setdefault("COLORTERM", "truecolor")
                process_env.setdefault("COLUMNS", str(self.terminal_columns))
                process_env.setdefault("LINES", str(self.terminal_rows))
                process = self._popen_factory(
                    self.command,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    bufsize=0,
                    close_fds=True,
                    start_new_session=_supports_process_groups(),
                    cwd=str(self.cwd),
                    env=process_env,
                )
            except Exception as error:
                self._last_error = str(error)
                _close_fd(master_fd)
                raise
            finally:
                _close_fd(slave_fd)
            _remember_process_group(process)
            os.set_blocking(master_fd, False)
            self.process = process
            self._master_fd = master_fd
            self._last_error = ""
            self._started_at = _now_iso()
            self._stopped_at = ""
            self._startup_drained = False
            return self.health()

    def deliver(self, events: list[dict[str, object]]) -> None:
        if not events:
            return
        self.start()
        self._drain_startup_output()
        self._message_source.begin_turn()
        self._message_turn_started = True
        for event in events:
            line = _room_input_text(event)
            self._send_line(line)
            event_id = clean_lobby_text(event.get("event_id"), limit=128)
            if event_id:
                self.last_seen_event_id = event_id

    def send(self, text: str) -> None:
        self.start()
        self._send_line(text)

    def send_keys(self, sequence: str) -> None:
        self.start()
        process, fd = self._state_snapshot()
        del process
        data = str(sequence or "").encode("utf-8")
        if data:
            os.write(fd, data)

    def read_output(
        self,
        *,
        timeout_seconds: float,
        on_delta: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        process, fd = self._state_snapshot()
        quiet = self.idle_quiet_seconds
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        chunks: list[bytes] = []
        total_bytes = 0
        last_read_at: float | None = None
        last_visible_content = ""
        final_snapshot = LiveCliMessageSnapshot()
        if not self._message_turn_started:
            self._message_source.begin_turn()
            self._message_turn_started = True
        while True:
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError(f"Live CLI runtime timed out after {timeout_seconds} seconds.")
            wait_until = deadline
            if chunks and last_read_at is not None:
                wait_until = min(deadline, last_read_at + quiet)
            readable = _select_readable(fd, min(0.1, max(0.0, wait_until - now)))
            if not readable:
                final_snapshot = self._poll_message_source(
                    b"".join(chunks),
                    quiet=bool(chunks and last_read_at is not None and time.monotonic() >= last_read_at + quiet),
                    previous=final_snapshot,
                    on_delta=on_delta,
                    last_visible_content_ref=[last_visible_content],
                )
                if final_snapshot.complete:
                    self._message_turn_started = False
                    return self._output_message_from_snapshot(final_snapshot)
                if final_snapshot.content:
                    last_visible_content = final_snapshot.content
                if not self._fd_is_current(fd):
                    raise RuntimeError("Live CLI runtime stopped while reading.")
                if process.poll() is not None:
                    raise RuntimeError(f"Live CLI runtime exited with return code {process.returncode}.")
                now = time.monotonic()
                if chunks and last_read_at is not None and now >= last_read_at + quiet:
                    if getattr(self._message_source, "strict", False):
                        if getattr(self._message_source, "fail_on_quiet_without_message", False):
                            raise LiveCliMessageExtractionError(
                                f"{self.agent_id} did not expose a clean assistant message in its transcript."
                            )
                        continue
                    self._message_turn_started = False
                    return self._output_message(b"".join(chunks))
                continue
            chunk = _read_chunk(fd, process)
            if not chunk:
                if process.poll() is not None:
                    raise RuntimeError(f"Live CLI runtime exited with return code {process.returncode}.")
                if not self._fd_is_current(fd):
                    raise RuntimeError("Live CLI runtime stopped while reading.")
                continue
            chunks.append(chunk)
            total_bytes += len(chunk)
            last_read_at = time.monotonic()
            final_snapshot = self._poll_message_source(
                b"".join(chunks),
                quiet=False,
                previous=final_snapshot,
                on_delta=on_delta,
                last_visible_content_ref=[last_visible_content],
            )
            if final_snapshot.content:
                last_visible_content = final_snapshot.content
            if final_snapshot.complete:
                self._message_turn_started = False
                return self._output_message_from_snapshot(final_snapshot)
            if total_bytes > self.max_output_bytes:
                raise ValueError(f"Live CLI output exceeded {self.max_output_bytes} bytes.")

    def read_available(self, *, timeout_seconds: float = 0.0) -> dict[str, object]:
        process, fd = self._state_snapshot()
        chunks: list[bytes] = []
        total_bytes = 0
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while True:
            wait = max(0.0, deadline - time.monotonic()) if not chunks else 0.0
            readable = _select_readable(fd, wait)
            if not readable:
                break
            chunk = _read_chunk(fd, process)
            if not chunk:
                break
            chunks.append(chunk)
            total_bytes += len(chunk)
            if total_bytes > self.max_output_bytes:
                raise ValueError(f"Live CLI output exceeded {self.max_output_bytes} bytes.")
        return self._output_message(b"".join(chunks), message_only=False)

    def interrupt(self) -> None:
        process, fd = self._state_snapshot()
        del process
        os.write(fd, b"\x03")

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        with self._lock:
            process = self.process
            master_fd = self._master_fd
            self.process = None
            self._master_fd = None
        if master_fd is not None:
            _close_fd(master_fd)
        if process is not None:
            if process.poll() is None:
                _terminate_process(process, timeout_seconds=timeout_seconds)
            _terminate_process_group_children(process, timeout_seconds=timeout_seconds)
        with self._lock:
            self._stopped_at = _now_iso()

    def restart(self) -> dict[str, object]:
        self.stop()
        return self.start()

    def health(self) -> dict[str, object]:
        with self._lock:
            process = self.process
            last_error = self._last_error
            started_at = self._started_at
            stopped_at = self._stopped_at
            resolved_executable = self._resolved_executable or _resolve_executable(self.command)
        returncode = process.poll() if process is not None else None
        running = process is not None and returncode is None
        return {
            "agent_id": self.agent_id,
            "runtime_kind": "live_cli",
            "command_configured": list(self.command),
            "command_display": " ".join(self.command),
            "resolved_executable": resolved_executable,
            "cwd": str(self.cwd),
            "workspace_dir": str(self.cwd),
            "session_dir": "",
            "pty": True,
            "transport": "pty",
            "is_one_shot": False,
            "input_mode": self.input_mode,
            "terminal_rows": self.terminal_rows,
            "terminal_columns": self.terminal_columns,
            "startup_quiet_seconds": self.startup_quiet_seconds,
            "startup_timeout_seconds": self.startup_timeout_seconds,
            **self._message_source.describe(),
            "running": running,
            "stopped": not running,
            "pid": process.pid if process is not None else None,
            "returncode": returncode,
            "last_error": last_error,
            "last_seen_event_id": self.last_seen_event_id,
            "started_at": started_at,
            "stopped_at": stopped_at,
        }

    def _send_line(self, text: str) -> None:
        process, fd = self._state_snapshot()
        del process
        payload = str(text or "")
        data = _terminal_input_bytes(payload, input_mode=self.input_mode, submit_newline=self.submit_newline)
        offset = 0
        while offset < len(data):
            writable = _select_writable(fd, 5.0)
            if not writable:
                raise TimeoutError("Timed out writing to Live CLI runtime.")
            try:
                written = os.write(fd, data[offset:])
            except OSError as error:
                raise RuntimeError("Live CLI runtime closed while writing.") from error
            if written <= 0:
                raise RuntimeError("Live CLI runtime closed while writing.")
            offset += written

    def _output_message(self, response: bytes, *, message_only: bool = True) -> dict[str, object]:
        return {
            "actor_id": self.agent_id,
            "actor_type": "agent",
            "kind": "agent_message",
            "content": self._message_content(response) if message_only else _clean_terminal_text(response),
        }

    def _message_content(self, response: bytes) -> str:
        return extract_live_cli_terminal_message(response)

    def _poll_message_source(
        self,
        response: bytes,
        *,
        quiet: bool,
        previous: LiveCliMessageSnapshot,
        on_delta: Callable[[str], None] | None,
        last_visible_content_ref: list[str],
    ) -> LiveCliMessageSnapshot:
        snapshot = self._message_source.poll(response, quiet=quiet)
        if not snapshot.content:
            return previous
        last_visible_content = last_visible_content_ref[0] if last_visible_content_ref else ""
        if on_delta is not None:
            if not last_visible_content:
                on_delta(snapshot.content)
            elif snapshot.content.startswith(last_visible_content):
                delta = snapshot.content[len(last_visible_content) :]
                if delta:
                    on_delta(delta)
        if last_visible_content_ref:
            last_visible_content_ref[0] = snapshot.content
        return snapshot

    def _output_message_from_snapshot(self, snapshot: LiveCliMessageSnapshot) -> dict[str, object]:
        return {
            "actor_id": self.agent_id,
            "actor_type": "agent",
            "kind": "agent_message",
            "content": snapshot.content,
            "metadata": {
                "message_source": snapshot.source_kind,
                "message_source_path": snapshot.source,
            },
        }

    def _state_snapshot(self) -> tuple[subprocess.Popen[bytes], int]:
        with self._lock:
            self._ensure_running_locked()
            assert self.process is not None
            assert self._master_fd is not None
            return self.process, self._master_fd

    def _drain_startup_output(self) -> None:
        if self._startup_drained:
            return
        if self.startup_timeout_seconds <= 0:
            self._startup_drained = True
            return
        process, fd = self._state_snapshot()
        deadline = time.monotonic() + self.startup_timeout_seconds
        last_read_at: float | None = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Live CLI runtime exited with return code {process.returncode}.")
            readable = _select_readable(fd, 0.1)
            now = time.monotonic()
            if readable:
                _read_chunk(fd, process)
                last_read_at = now
                continue
            if last_read_at is None:
                continue
            if self.startup_quiet_seconds <= 0 or now - last_read_at >= self.startup_quiet_seconds:
                break
        self._startup_drained = True

    def _fd_is_current(self, fd: int) -> bool:
        with self._lock:
            return self.process is not None and self._master_fd == fd

    def _is_running_locked(self) -> bool:
        return self.process is not None and self.process.poll() is None and self._master_fd is not None

    def _ensure_running_locked(self) -> None:
        if not self._is_running_locked():
            raise RuntimeError("Live CLI runtime is not running.")


class ApiRuntime:
    """Future AgentRuntime slot for API providers.

    The CLI-first MVP must not be reduced to complete(prompt) -> text to make
    APIs convenient. API providers need to implement the same room-event runtime
    contract before they can participate in #general.
    """

    def __init__(self, agent_id: str) -> None:
        self.agent_id = clean_lobby_text(agent_id, limit=128)
        self.last_seen_event_id = ""

    def start(self) -> dict[str, object]:
        return self.health()

    def deliver(self, events: list[dict[str, object]]) -> None:
        if events:
            event_id = clean_lobby_text(events[-1].get("event_id"), limit=128)
            if event_id:
                self.last_seen_event_id = event_id

    def read_output(
        self,
        *,
        timeout_seconds: float,
        on_delta: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        del timeout_seconds, on_delta
        raise RuntimeError("API runtime is a later AgentRuntime implementation; LiveCliRuntime is the MVP path.")

    def interrupt(self) -> None:
        return

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        del timeout_seconds
        return

    def health(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "running": False,
            "stopped": True,
            "status": "unsupported",
            "last_seen_event_id": self.last_seen_event_id,
        }


@dataclass
class AgentRuntimeBinding:
    agent_id: str
    runtime: AgentRuntime
    default_responder: bool = True


class RoomScheduler:
    """Server-side router from #general events to persistent runtimes."""

    def __init__(
        self,
        room: GeneralRoomEventStore,
        agents: list[AgentRuntimeBinding],
        *,
        read_timeout_seconds: float = 120.0,
        max_agent_relay_depth: int = 1,
        max_replies_per_source: int = 1,
        agent_cooldown_seconds: float = 0.0,
        agent_state_listener: Callable[[str, dict[str, object]], None] | None = None,
        latency_listener: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        self.room = room
        self.agents = {clean_lobby_text(agent.agent_id, limit=128): agent for agent in agents}
        self.read_timeout_seconds = max(0.1, float(read_timeout_seconds))
        self.max_agent_relay_depth = max(0, int(max_agent_relay_depth))
        self.max_replies_per_source = max(1, int(max_replies_per_source))
        self.agent_cooldown_seconds = max(0.0, float(agent_cooldown_seconds))
        self._lock = threading.RLock()
        self._busy: set[str] = set()
        self._threads: list[threading.Thread] = []
        self._last_reply_at: dict[str, float] = {}
        self._reply_counts: dict[tuple[str, str], int] = {}
        self._last_input_event_id: dict[str, str] = {}
        self._last_output_event_id: dict[str, str] = {}
        self._turn_counts: dict[str, int] = {}
        self._last_errors: dict[str, str] = {}
        self._latencies: dict[str, dict[str, object]] = {}
        self.agent_state_listener = agent_state_listener
        self.latency_listener = latency_listener

    def dispatch_new_events(self) -> list[dict[str, object]]:
        dispatched: list[dict[str, object]] = []
        started_agents: list[str] = []
        with self._lock:
            for agent_id, binding in self.agents.items():
                if agent_id in self._busy:
                    continue
                event = self._next_actionable_event(binding)
                if event is None:
                    continue
                source_event_id = clean_lobby_text(event.get("event_id"), limit=128)
                if not self._reserve_reply(agent_id, source_event_id):
                    continue
                self._busy.add(agent_id)
                thread = threading.Thread(target=self._run_agent_event, args=(binding, event), daemon=True)
                self._threads.append(thread)
                dispatched.append(event)
                thread.start()
                started_agents.append(agent_id)
        for agent_id in started_agents:
            self._notify_agent_state(agent_id)
        return dispatched

    def wait_for_idle(self, *, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while True:
            with self._lock:
                threads = list(self._threads)
            for thread in threads:
                remaining = max(0.0, deadline - time.monotonic())
                thread.join(timeout=min(0.05, remaining))
            with self._lock:
                self._threads = [thread for thread in self._threads if thread.is_alive()]
                if not self._busy and not self._threads:
                    return True
            if time.monotonic() >= deadline:
                return False

    def stop_all(self) -> None:
        for binding in self.agents.values():
            binding.runtime.stop()
        self.wait_for_idle(timeout_seconds=1.0)

    def agent_statuses(self) -> dict[str, dict[str, object]]:
        with self._lock:
            busy = set(self._busy)
        statuses: dict[str, dict[str, object]] = {}
        for agent_id, binding in self.agents.items():
            health = binding.runtime.health()
            last_error = self._last_errors.get(agent_id, "")
            if agent_id in busy:
                status = "busy"
            elif last_error and not health.get("running"):
                status = "error"
            elif health.get("running"):
                status = "idle"
            else:
                status = "stopped"
            health["status"] = status
            health["last_input_event_id"] = self._last_input_event_id.get(agent_id, "")
            health["last_output_event_id"] = self._last_output_event_id.get(agent_id, "")
            health["turn_count"] = self._turn_counts.get(agent_id, 0)
            health["last_error"] = last_error or str(health.get("last_error") or "")
            health["latency"] = dict(self._latencies.get(agent_id, {}))
            statuses[agent_id] = health
        return statuses

    def latency_payload(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return {agent_id: dict(latency) for agent_id, latency in self._latencies.items()}

    def _notify_agent_state(self, agent_id: str) -> None:
        if self.agent_state_listener is None:
            return
        try:
            self.agent_state_listener(agent_id, dict(self.agent_statuses().get(agent_id, {})))
        except Exception:
            return

    def _notify_latency(self, agent_id: str) -> None:
        if self.latency_listener is None:
            return
        with self._lock:
            latency = dict(self._latencies.get(agent_id, {}))
        try:
            self.latency_listener(agent_id, latency)
        except Exception:
            return

    def _next_actionable_event(self, binding: AgentRuntimeBinding) -> dict[str, object] | None:
        runtime = binding.runtime
        events = self.room.read_events(after=runtime.last_seen_event_id)
        for event in events:
            event_id = clean_lobby_text(event.get("event_id"), limit=128)
            if self._event_targets_agent(event, binding):
                return event
            if event_id:
                runtime.last_seen_event_id = event_id
        return None

    def _event_targets_agent(self, event: dict[str, object], binding: AgentRuntimeBinding) -> bool:
        agent_id = clean_lobby_text(binding.agent_id, limit=128)
        kind = clean_lobby_text(event.get("kind"), limit=64)
        actor_id = clean_lobby_text(event.get("actor_id"), limit=128)
        content = clean_lobby_text(event.get("content"), limit=12000)
        mentions = _mentions(content)
        if kind == "user_message":
            if not mentions:
                return bool(binding.default_responder)
            return "all" in mentions or agent_id.casefold() in mentions
        if kind != "agent_message":
            return False
        if actor_id == agent_id:
            return False
        if _safe_int(_event_metadata(event).get("relay_depth")) >= self.max_agent_relay_depth:
            return False
        return "all" in mentions or agent_id.casefold() in mentions

    def _reserve_reply(self, agent_id: str, source_event_id: str) -> bool:
        if not source_event_id:
            return True
        now = time.monotonic()
        if self.agent_cooldown_seconds > 0:
            last_reply_at = self._last_reply_at.get(agent_id, 0.0)
            if now - last_reply_at < self.agent_cooldown_seconds:
                return False
        key = (agent_id, source_event_id)
        count = self._reply_counts.get(key, 0)
        if count >= self.max_replies_per_source:
            return False
        self._reply_counts[key] = count + 1
        self._last_reply_at[agent_id] = now
        return True

    def _run_agent_event(self, binding: AgentRuntimeBinding, source_event: dict[str, object]) -> None:
        agent_id = clean_lobby_text(binding.agent_id, limit=128)
        runtime = binding.runtime
        source_event_id = clean_lobby_text(source_event.get("event_id"), limit=128)
        relay_depth = _safe_int(_event_metadata(source_event).get("relay_depth")) + 1
        latency, latency_ticks = _new_latency(source_event)
        try:
            runtime.start()
            input_text = _room_input_text(source_event)
            _mark_latency(latency, latency_ticks, "dispatch_started_at")
            _mark_latency(latency, latency_ticks, "input_write_started_at")
            self.room.append_agent_input(agent_id, input_text, source_event_id=source_event_id)
            with self._lock:
                self._last_input_event_id[agent_id] = source_event_id

            def append_delta(delta: str) -> None:
                if not latency.get("first_output_at"):
                    _mark_latency(latency, latency_ticks, "first_output_at")
                _mark_latency(latency, latency_ticks, "last_output_at")
                self.room.append_agent_delta(agent_id, delta, source_event_id=source_event_id)

            runtime.deliver([source_event])
            _mark_latency(latency, latency_ticks, "input_write_completed_at")
            output = runtime.read_output(timeout_seconds=self.read_timeout_seconds, on_delta=append_delta)
            content = clean_lobby_text(output.get("content"), limit=12000)
            output_metadata = output.get("metadata") if isinstance(output.get("metadata"), dict) else {}
            if content and not latency.get("first_output_at"):
                _mark_latency(latency, latency_ticks, "first_output_at")
                _mark_latency(latency, latency_ticks, "last_output_at")
            _mark_latency(latency, latency_ticks, "quiet_detected_at")
            _mark_latency(latency, latency_ticks, "turn_completed_at")
            _complete_latency(latency, latency_ticks)
            output_event = self.room.append_agent_message(
                agent_id,
                content,
                source_event_id=source_event_id,
                relay_depth=relay_depth,
                metadata={**dict(output_metadata), "latency": dict(latency)},
            )
            with self._lock:
                self._last_output_event_id[agent_id] = clean_lobby_text(output_event.get("event_id"), limit=128)
                self._turn_counts[agent_id] = self._turn_counts.get(agent_id, 0) + 1
                self._last_errors[agent_id] = ""
                self._latencies[agent_id] = dict(latency)
        except Exception as error:
            _mark_latency(latency, latency_ticks, "turn_completed_at")
            _complete_latency(latency, latency_ticks)
            error_event = self.room.append_agent_error(
                agent_id,
                str(error),
                source_event_id=source_event_id,
                metadata={"latency": dict(latency)},
            )
            with self._lock:
                self._last_output_event_id[agent_id] = clean_lobby_text(error_event.get("event_id"), limit=128)
                self._last_errors[agent_id] = str(error)
                self._latencies[agent_id] = dict(latency)
        finally:
            with self._lock:
                self._busy.discard(agent_id)
            self._notify_agent_state(agent_id)
            self._notify_latency(agent_id)


def live_cli_supported() -> bool:
    return (
        pty is not None
        and termios is not None
        and select is not None
        and hasattr(pty, "openpty")
        and hasattr(termios, "tcgetattr")
        and hasattr(termios, "tcsetattr")
    )


def _room_input_text(event: dict[str, object]) -> str:
    actor = clean_lobby_text(event.get("actor_id"), limit=128) or "unknown"
    content = clean_lobby_text(event.get("content"), limit=12000)
    return f"#general {actor}: {content}"


def _event_metadata(event: dict[str, object]) -> dict[str, object]:
    metadata = event.get("metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def _clean_metadata(metadata: dict[str, object]) -> dict[str, object]:
    clean: dict[str, object] = {}
    for key, value in metadata.items():
        if value in (None, "", [], {}):
            continue
        if key in {"source_event_id", "queued_at"}:
            clean[key] = clean_lobby_text(value, limit=128)
        elif key == "latency" and isinstance(value, dict):
            clean[key] = {str(item_key): item_value for item_key, item_value in value.items()}
        else:
            clean[key] = value
    return clean


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_latency(source_event: dict[str, object]) -> tuple[dict[str, object], dict[str, datetime]]:
    metadata = _event_metadata(source_event)
    queued_at = clean_lobby_text(metadata.get("queued_at"), limit=128) or clean_lobby_text(
        source_event.get("created_at"), limit=128
    )
    if not queued_at:
        queued_at = _now_iso()
    latency: dict[str, object] = {
        "queued_at": queued_at,
        "dispatch_started_at": "",
        "input_write_started_at": "",
        "input_write_completed_at": "",
        "first_output_at": "",
        "last_output_at": "",
        "quiet_detected_at": "",
        "turn_completed_at": "",
        "queue_delay_ms": 0,
        "input_write_ms": 0,
        "ttfo_ms": 0,
        "stream_ms": 0,
        "quiet_wait_ms": 0,
        "total_turn_ms": 0,
    }
    queued_datetime = _parse_iso(queued_at) or datetime.now(UTC)
    return latency, {"queued_at": queued_datetime}


def _mark_latency(latency: dict[str, object], ticks: dict[str, datetime], key: str) -> None:
    now = datetime.now(UTC)
    ticks[key] = now
    latency[key] = now.isoformat()


def _complete_latency(latency: dict[str, object], ticks: dict[str, datetime]) -> None:
    latency["queue_delay_ms"] = _duration_ms(ticks, "queued_at", "dispatch_started_at")
    latency["input_write_ms"] = _duration_ms(ticks, "input_write_started_at", "input_write_completed_at")
    latency["ttfo_ms"] = _duration_ms(ticks, "input_write_completed_at", "first_output_at")
    latency["stream_ms"] = _duration_ms(ticks, "first_output_at", "last_output_at")
    latency["quiet_wait_ms"] = _duration_ms(ticks, "last_output_at", "quiet_detected_at")
    latency["total_turn_ms"] = _duration_ms(ticks, "queued_at", "turn_completed_at")


def _duration_ms(ticks: dict[str, datetime], start: str, end: str) -> int:
    start_time = ticks.get(start)
    end_time = ticks.get(end)
    if start_time is None or end_time is None:
        return 0
    return max(0, int((end_time - start_time).total_seconds() * 1000))


def _parse_iso(value: object) -> datetime | None:
    text = clean_lobby_text(value, limit=128)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _resolve_executable(command: list[str]) -> str:
    if not command:
        return ""
    executable = str(command[0] or "")
    if not executable:
        return ""
    if "/" in executable or "\\" in executable:
        path = Path(executable).expanduser()
        return str(path.resolve()) if path.exists() else ""
    return shutil.which(executable) or ""


def _mentions(content: str) -> set[str]:
    return {match.casefold() for match in re.findall(r"@([A-Za-z0-9_.-]+)", content or "")}


def _configure_slave_terminal(fd: int, *, rows: int = 40, columns: int = 120) -> None:
    _set_terminal_window_size(fd, rows=rows, columns=columns)
    if termios is None:
        return
    attrs = termios.tcgetattr(fd)
    attrs[3] = attrs[3] & ~(termios.ECHO | termios.ICANON)
    attrs[6][termios.VMIN] = 1
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def _set_terminal_window_size(fd: int, *, rows: int, columns: int) -> None:
    if termios is None:
        return
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", int(rows), int(columns), 0, 0))
    except OSError:
        return


def _terminal_input_bytes(text: str, *, input_mode: str, submit_newline: str) -> bytes:
    payload = str(text or "")
    newline = submit_newline or "\n"
    if clean_lobby_text(input_mode, limit=64) == "bracketed_paste":
        return f"\x1b[200~{payload}\x1b[201~{newline}".encode("utf-8")
    if not payload.endswith(newline):
        payload += newline
    return payload.encode("utf-8")


def _clean_terminal_text(response: bytes) -> str:
    text = strip_terminal_ansi(response)
    return text.strip()


def _select_readable(fd: int | None, timeout_seconds: float) -> bool:
    if fd is None or select is None:
        return False
    try:
        readable, _, _ = select.select([fd], [], [], max(0.0, float(timeout_seconds)))
    except OSError:
        return False
    return bool(readable)


def _select_writable(fd: int | None, timeout_seconds: float) -> bool:
    if fd is None or select is None:
        return False
    try:
        _, writable, _ = select.select([], [fd], [], max(0.0, float(timeout_seconds)))
    except OSError:
        return False
    return bool(writable)


def _read_chunk(fd: int, process: subprocess.Popen[bytes]) -> bytes:
    try:
        return os.read(fd, 4096)
    except BlockingIOError:
        return b""
    except OSError as error:
        if process.poll() is not None:
            return b""
        raise RuntimeError("Live CLI runtime closed while reading.") from error


def _close_fd(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass


def _supports_process_groups() -> bool:
    return hasattr(os, "killpg") and hasattr(os, "setsid") and hasattr(os, "getpgid")


def _remember_process_group(process: subprocess.Popen[bytes]) -> None:
    if not _supports_process_groups():
        return
    try:
        if os.getpgid(process.pid) == process.pid:
            setattr(process, "_agentsassemble_process_group_pid", process.pid)
    except OSError:
        return


def _process_group_pid(process: subprocess.Popen[bytes]) -> int | None:
    pgid = getattr(process, "_agentsassemble_process_group_pid", None)
    return pgid if isinstance(pgid, int) and pgid > 0 else None


def _terminate_process(process: subprocess.Popen[bytes], *, timeout_seconds: float) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout_seconds)
    except ProcessLookupError:
        return


def _terminate_process_group_children(process: subprocess.Popen[bytes], *, timeout_seconds: float) -> None:
    pgid = _process_group_pid(process)
    if pgid is None or not hasattr(os, "killpg"):
        return
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.01)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# Transitional aliases for the earlier local prototype names. The new MVP
# surface is AgentRuntime, LiveCliRuntime, GeneralRoomEventStore, RoomScheduler.
LiveCliSession = LiveCliRuntime
GeneralRoomEventLog = GeneralRoomEventStore
LiveCliRoomScheduler = RoomScheduler
LiveCliAgent = AgentRuntimeBinding
