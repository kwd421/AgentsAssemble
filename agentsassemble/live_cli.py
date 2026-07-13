from __future__ import annotations

import os
import signal
import shutil
import subprocess
import threading
import time
try:
    import fcntl
    import struct
except ImportError:  # pragma: no cover - Windows uses ConPTY
    fcntl = None  # type: ignore[assignment]
    struct = None  # type: ignore[assignment]
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from agentsassemble.live_cli_output import extract_live_cli_terminal_message, strip_terminal_ansi
from agentsassemble.live_cli_transcripts import (
    LiveCliMessageExtractionError,
    LiveCliMessageSource,
    LiveCliMessageSnapshot,
    make_live_cli_message_source,
)
from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.process_environment import sanitized_provider_environment

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
PARENT_AGENT_SESSION_ENV_KEYS = {
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SESSION_ID",
    "CODEX_CI",
    "CODEX_INTERNAL_ORIGINATOR_OVERRIDE",
    "CODEX_PERMISSION_PROFILE",
    "CODEX_SHELL",
    "CODEX_THREAD_ID",
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
        on_activity: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        ...

    def interrupt(self) -> None:
        ...

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        ...

    def health(self) -> dict[str, object]:
        ...


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
        submit_delay_seconds: float = 0.1,
        input_mode: str = "line",
        terminal_rows: int = 40,
        terminal_columns: int = 120,
        startup_quiet_seconds: float = 0.0,
        startup_timeout_seconds: float = 0.0,
        startup_accept_contains: str = "",
        startup_accept_keys: str = "\r",
        startup_input: str = "",
        max_output_bytes: int = 256_000,
        message_source: LiveCliMessageSource | None = None,
        profile_settings: dict[str, object] | None = None,
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
        self.submit_delay_seconds = max(0.0, float(submit_delay_seconds or 0.0))
        self.input_mode = clean_lobby_text(input_mode, limit=64) or "line"
        if self.input_mode not in {"line", "bracketed_paste"}:
            raise ValueError("input_mode must be line or bracketed_paste.")
        self.terminal_rows = max(10, int(terminal_rows or 40))
        self.terminal_columns = max(40, int(terminal_columns or 120))
        self.startup_quiet_seconds = max(0.0, float(startup_quiet_seconds or 0.0))
        self.startup_timeout_seconds = max(0.0, float(startup_timeout_seconds or 0.0))
        self.startup_accept_contains = str(startup_accept_contains or "")
        self.startup_accept_keys = str(startup_accept_keys or "\r")
        self.startup_input = str(startup_input or "")
        self.max_output_bytes = max(1, int(max_output_bytes))
        self.profile_settings = {
            key: clean_lobby_text(value, limit=256)
            for key, value in dict(profile_settings or {}).items()
            if clean_lobby_text(value, limit=256)
        }
        self._message_source = message_source or make_live_cli_message_source(
            self.agent_id,
            self.command,
            cwd=self.cwd,
        )
        self._message_turn_started = False
        self._needs_terminal_settle = False
        self.last_seen_event_id = ""
        self._lock = threading.RLock()
        self._master_fd: int | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self._last_error = ""
        self._started_at = ""
        self._stopped_at = ""
        self._resolved_executable = ""
        self._startup_drained = False
        self._startup_input_sent = False
        self._terminal_byte_count = 0
        self._terminal_tail = bytearray()

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
                process_env = sanitized_provider_environment(self.env)
                for key in PARENT_AGENT_SESSION_ENV_KEYS:
                    process_env.pop(key, None)
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
            self._startup_input_sent = False
            self._needs_terminal_settle = False
            self._terminal_byte_count = 0
            self._terminal_tail = bytearray()
            return self.health()

    def deliver(self, events: list[dict[str, object]]) -> None:
        if not events:
            return
        self.start()
        self._drain_startup_output()
        lines = [_room_input_text(event) for event in events]
        self._message_source.begin_turn(lines[0] if len(lines) == 1 else "")
        self._message_turn_started = True
        for event, line in zip(events, lines):
            self._send_line(line)
            event_id = clean_lobby_text(event.get("event_id"), limit=128)
            if event_id:
                self.last_seen_event_id = event_id

    def send(self, text: str) -> None:
        self.start()
        self._drain_startup_output()
        self._message_source.begin_turn(text)
        self._message_turn_started = True
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
        on_activity: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        del on_activity
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
                    self._needs_terminal_settle = True
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
                    self._needs_terminal_settle = True
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
            self._record_terminal_bytes(chunk)
            last_read_at = time.monotonic()
            terminal_error = _terminal_fatal_error(bytes(self._terminal_tail))
            if terminal_error and getattr(self._message_source, "strict", False):
                raise LiveCliMessageExtractionError(terminal_error)
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
                self._needs_terminal_settle = True
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
            self._record_terminal_bytes(chunk)
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
            terminal_byte_count = self._terminal_byte_count
            terminal_tail = _terminal_diagnostic_tail(bytes(self._terminal_tail))
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
            "provider_session_active": running,
            "is_one_shot": False,
            "input_mode": self.input_mode,
            "submit_delay_seconds": self.submit_delay_seconds,
            "terminal_rows": self.terminal_rows,
            "terminal_columns": self.terminal_columns,
            "startup_quiet_seconds": self.startup_quiet_seconds,
            "startup_timeout_seconds": self.startup_timeout_seconds,
            "startup_accept_configured": bool(self.startup_accept_contains),
            "parent_agent_session_env_removed": sorted(PARENT_AGENT_SESSION_ENV_KEYS),
            "terminal_byte_count": terminal_byte_count,
            "terminal_tail": terminal_tail,
            **self._message_source.describe(),
            "running": running,
            "stopped": not running,
            "pid": process.pid if process is not None else None,
            "returncode": returncode,
            "last_error": last_error,
            "last_seen_event_id": self.last_seen_event_id,
            "started_at": started_at,
            "stopped_at": stopped_at,
            **self.profile_settings,
        }

    def _send_line(self, text: str) -> None:
        process, fd = self._state_snapshot()
        if self._needs_terminal_settle:
            self._drain_pending_terminal_output(
                process,
                fd,
                quiet_seconds=min(1.0, self.idle_quiet_seconds),
                timeout_seconds=5.0,
            )
            self._needs_terminal_settle = False
        else:
            self._drain_terminal_available(process, fd)
        payload = str(text or "")
        chunks = _terminal_input_chunks(payload, input_mode=self.input_mode, submit_newline=self.submit_newline)
        for index, data in enumerate(chunks):
            if index and self.submit_delay_seconds:
                time.sleep(self.submit_delay_seconds)
            offset = 0
            write_deadline = time.monotonic() + 5.0
            while offset < len(data):
                if process.poll() is not None or not self._fd_is_current(fd):
                    raise RuntimeError("Live CLI runtime closed while writing.")
                if _select_writable(fd, 0.05):
                    try:
                        written = os.write(fd, data[offset:])
                    except OSError as error:
                        raise RuntimeError("Live CLI runtime closed while writing.") from error
                    if written <= 0:
                        raise RuntimeError("Live CLI runtime closed while writing.")
                    offset += written
                    continue
                self._drain_terminal_available(process, fd)
                if time.monotonic() >= write_deadline:
                    raise TimeoutError("Timed out writing to Live CLI runtime.")

    def _drain_pending_terminal_output(
        self,
        process: subprocess.Popen[bytes],
        fd: int,
        *,
        quiet_seconds: float = 0.05,
        timeout_seconds: float = 2.0,
    ) -> int:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        quiet_at = time.monotonic() + max(0.0, quiet_seconds)
        total = 0
        while time.monotonic() < deadline:
            drained = self._drain_terminal_available(process, fd)
            if drained:
                total += drained
                quiet_at = time.monotonic() + max(0.0, quiet_seconds)
                continue
            if time.monotonic() >= quiet_at:
                break
            time.sleep(0.005)
        return total

    def _drain_terminal_available(
        self,
        process: subprocess.Popen[bytes],
        fd: int,
        *,
        max_bytes: int = 1_000_000,
    ) -> int:
        total = 0
        while total < max_bytes and _select_readable(fd, 0.0):
            chunk = _read_chunk(fd, process)
            if not chunk:
                break
            total += len(chunk)
            self._record_terminal_bytes(chunk)
        return total

    def _output_message(self, response: bytes, *, message_only: bool = True) -> dict[str, object]:
        return {
            "outcome": "message",
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
        if snapshot.error:
            raise LiveCliMessageExtractionError(snapshot.error)
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
            "outcome": "message",
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
        process, fd = self._state_snapshot()
        deadline = time.monotonic() + self.startup_timeout_seconds
        last_read_at: float | None = None
        startup_output = bytearray()
        startup_accepted = False
        terminal_queries_answered: set[str] = set()
        while self.startup_timeout_seconds > 0 and time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Live CLI runtime exited with return code {process.returncode}.")
            readable = _select_readable(fd, 0.1)
            now = time.monotonic()
            if readable:
                chunk = _read_chunk(fd, process)
                if chunk:
                    startup_output.extend(chunk)
                    self._record_terminal_bytes(chunk)
                    response = _terminal_query_response(bytes(startup_output), terminal_queries_answered)
                    if response:
                        os.write(fd, response)
                    if len(startup_output) > 64_000:
                        del startup_output[:-64_000]
                last_read_at = now
                if (
                    not startup_accepted
                    and self.startup_accept_contains
                    and self.startup_accept_contains.casefold()
                    in _clean_terminal_text(bytes(startup_output)).casefold()
                ):
                    os.write(fd, self.startup_accept_keys.encode("utf-8"))
                    startup_accepted = True
                continue
            if last_read_at is None:
                continue
            if self.startup_quiet_seconds <= 0 or now - last_read_at >= self.startup_quiet_seconds:
                break
        if self.startup_input and not self._startup_input_sent:
            os.write(fd, self.startup_input.encode("utf-8"))
            self._startup_input_sent = True
            command_deadline = time.monotonic() + 3.0
            last_command_read = time.monotonic()
            while time.monotonic() < command_deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Live CLI runtime exited with return code {process.returncode}.")
                if _select_readable(fd, 0.05):
                    chunk = _read_chunk(fd, process)
                    if chunk:
                        self._record_terminal_bytes(chunk)
                        last_command_read = time.monotonic()
                    continue
                if time.monotonic() - last_command_read >= max(0.2, self.startup_quiet_seconds):
                    break
        self._startup_drained = True

    def _record_terminal_bytes(self, chunk: bytes) -> None:
        if not chunk:
            return
        with self._lock:
            self._terminal_byte_count += len(chunk)
            self._terminal_tail.extend(chunk)
            if len(self._terminal_tail) > 32_000:
                del self._terminal_tail[:-32_000]

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
        on_activity: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        del timeout_seconds, on_delta, on_activity
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
            "transport": "unsupported",
            "provider_session_active": False,
            "started_at": None,
            "stopped": True,
            "status": "unsupported",
            "last_seen_event_id": self.last_seen_event_id,
        }


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


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _terminal_query_response(chunk: bytes, answered: set[str] | None = None) -> bytes:
    """Answer the small terminal capability set interactive TUIs query at startup."""
    responses: list[bytes] = []
    seen = answered if answered is not None else set()
    queries = (
        ("cursor", b"\x1b[6n", b"\x1b[1;1R"),
        ("device", b"\x1b[c", b"\x1b[?1;2c"),
        ("keyboard", b"\x1b[?u", b"\x1b[?0u"),
        ("foreground", b"\x1b]10;?\x1b\\", b"\x1b]10;rgb:ffff/ffff/ffff\x1b\\"),
        ("background", b"\x1b]11;?\x1b\\", b"\x1b]11;rgb:0000/0000/0000\x1b\\"),
    )
    for name, query, response in queries:
        if name not in seen and query in chunk:
            responses.append(response)
            seen.add(name)
    return b"".join(responses)


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
    if termios is None or fcntl is None or struct is None:
        return
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", int(rows), int(columns), 0, 0))
    except OSError:
        return


def _terminal_input_chunks(text: str, *, input_mode: str, submit_newline: str) -> tuple[bytes, ...]:
    payload = str(text or "")
    newline = submit_newline or "\n"
    if clean_lobby_text(input_mode, limit=64) == "bracketed_paste":
        return (f"\x1b[200~{payload}\x1b[201~".encode("utf-8"), newline.encode("utf-8"))
    if not payload.endswith(newline):
        payload += newline
    return (payload.encode("utf-8"),)


def _clean_terminal_text(response: bytes) -> str:
    text = strip_terminal_ansi(response)
    return text.strip()


def _terminal_fatal_error(response: bytes) -> str:
    folded = " ".join(_clean_terminal_text(response).casefold().split())
    if "invalid authentication credentials" in folded or "api error: 401" in folded:
        return "Provider authentication failed: run the provider's interactive login command."
    if "authentication required" in folded or "not logged in" in folded:
        return "Provider authentication is required."
    return ""


def _terminal_diagnostic_tail(response: bytes) -> str:
    # Normal TUI capture can contain the room prompt and account display data.
    # Persist only a classified operational error, never the screen contents.
    return _terminal_fatal_error(response)


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


LiveCliSession = LiveCliRuntime
