from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

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
        self.events_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def events_path(self) -> Path:
        return self.output_root / "rooms" / self.room_id / "events.jsonl"

    def append_user_message(self, actor_id: str, content: str) -> dict[str, object]:
        return self.append_event("user_message", actor_id=actor_id, actor_type="user", content=content)

    def append_agent_input(self, agent_id: str, content: str, *, source_event_id: str) -> dict[str, object]:
        return self.append_event(
            "agent_input",
            actor_id=agent_id,
            actor_type="agent",
            content=content,
            source_event_id=source_event_id,
        )

    def append_agent_delta(self, agent_id: str, content: str, *, source_event_id: str) -> dict[str, object]:
        return self.append_event(
            "agent_delta",
            actor_id=agent_id,
            actor_type="agent",
            content=content,
            source_event_id=source_event_id,
        )

    def append_agent_message(
        self,
        agent_id: str,
        content: str,
        *,
        source_event_id: str,
        relay_depth: int = 0,
    ) -> dict[str, object]:
        return self.append_event(
            "agent_message",
            actor_id=agent_id,
            actor_type="agent",
            content=content,
            source_event_id=source_event_id,
            relay_depth=max(0, int(relay_depth or 0)),
        )

    def append_agent_error(self, agent_id: str, message: str, *, source_event_id: str = "") -> dict[str, object]:
        return self.append_event(
            "agent_error",
            actor_id=agent_id,
            actor_type="agent",
            content=message,
            source_event_id=source_event_id,
        )

    def append_system(self, message: str) -> dict[str, object]:
        return self.append_event("system", actor_id="system", actor_type="system", content=message)

    def append_event(
        self,
        kind: str,
        *,
        actor_id: str,
        actor_type: str,
        content: str,
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
        }
        for key, value in extra.items():
            if value in (None, "", [], {}):
                continue
            if key in {"source_event_id"}:
                event[key] = clean_lobby_text(value, limit=128)
            else:
                event[key] = value
        with self._lock, self.events_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
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
        max_output_bytes: int = 256_000,
    ) -> None:
        if not command:
            raise ValueError("Live CLI command is required.")
        self.agent_id = clean_lobby_text(agent_id, limit=128)
        if not self.agent_id:
            raise ValueError("agent_id is required.")
        self.command = list(command)
        self.cwd = Path(cwd).expanduser() if cwd else None
        self.env = dict(env or {})
        self._popen_factory = popen_factory
        self.idle_quiet_seconds = max(0.01, float(idle_quiet_seconds))
        self.submit_newline = submit_newline or "\n"
        self.max_output_bytes = max(1, int(max_output_bytes))
        self.last_seen_event_id = ""
        self._lock = threading.RLock()
        self._master_fd: int | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self._last_error = ""

    def start(self) -> dict[str, object]:
        with self._lock:
            if self._is_running_locked():
                return self.health()
            if not live_cli_supported():
                raise RuntimeError("PTY live CLI sessions are not available on this host.")
            assert pty is not None
            master_fd, slave_fd = pty.openpty()
            try:
                _configure_slave_terminal(slave_fd)
                process_env = os.environ.copy()
                process_env.update(self.env)
                process = self._popen_factory(
                    self.command,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    bufsize=0,
                    close_fds=True,
                    start_new_session=_supports_process_groups(),
                    cwd=str(self.cwd) if self.cwd is not None else None,
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
            return self.health()

    def deliver(self, events: list[dict[str, object]]) -> None:
        if not events:
            return
        self.start()
        for event in events:
            line = _room_input_text(event)
            self._send_line(line)
            event_id = clean_lobby_text(event.get("event_id"), limit=128)
            if event_id:
                self.last_seen_event_id = event_id

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
        while True:
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError(f"Live CLI runtime timed out after {timeout_seconds} seconds.")
            wait_until = deadline
            if chunks and last_read_at is not None:
                wait_until = min(deadline, last_read_at + quiet)
            readable = _select_readable(fd, max(0.0, wait_until - now))
            if not readable:
                if not self._fd_is_current(fd):
                    raise RuntimeError("Live CLI runtime stopped while reading.")
                if process.poll() is not None:
                    raise RuntimeError(f"Live CLI runtime exited with return code {process.returncode}.")
                now = time.monotonic()
                if chunks and last_read_at is not None and now >= last_read_at + quiet:
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
            delta = _clean_terminal_text(chunk)
            if delta and on_delta is not None:
                on_delta(delta)
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
        return self._output_message(b"".join(chunks))

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

    def restart(self) -> dict[str, object]:
        self.stop()
        return self.start()

    def health(self) -> dict[str, object]:
        with self._lock:
            process = self.process
            last_error = self._last_error
        returncode = process.poll() if process is not None else None
        running = process is not None and returncode is None
        return {
            "agent_id": self.agent_id,
            "running": running,
            "stopped": not running,
            "pid": process.pid if process is not None else None,
            "returncode": returncode,
            "last_error": last_error,
            "last_seen_event_id": self.last_seen_event_id,
        }

    def _send_line(self, text: str) -> None:
        process, fd = self._state_snapshot()
        del process
        payload = str(text or "")
        if not payload.endswith(self.submit_newline):
            payload += self.submit_newline
        data = payload.encode("utf-8")
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

    def _output_message(self, response: bytes) -> dict[str, object]:
        return {
            "actor_id": self.agent_id,
            "actor_type": "agent",
            "kind": "agent_message",
            "content": _clean_terminal_text(response),
        }

    def _state_snapshot(self) -> tuple[subprocess.Popen[bytes], int]:
        with self._lock:
            self._ensure_running_locked()
            assert self.process is not None
            assert self._master_fd is not None
            return self.process, self._master_fd

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

    def dispatch_new_events(self) -> list[dict[str, object]]:
        dispatched: list[dict[str, object]] = []
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
            health["status"] = "busy" if agent_id in busy else ("idle" if health.get("running") else "disconnected")
            statuses[agent_id] = health
        return statuses

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
        if _safe_int(event.get("relay_depth")) >= self.max_agent_relay_depth:
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
        relay_depth = _safe_int(source_event.get("relay_depth")) + 1
        try:
            runtime.start()
            input_text = _room_input_text(source_event)
            self.room.append_agent_input(agent_id, input_text, source_event_id=source_event_id)

            def append_delta(delta: str) -> None:
                self.room.append_agent_delta(agent_id, delta, source_event_id=source_event_id)

            runtime.deliver([source_event])
            output = runtime.read_output(timeout_seconds=self.read_timeout_seconds, on_delta=append_delta)
            content = clean_lobby_text(output.get("content"), limit=12000)
            self.room.append_agent_message(agent_id, content, source_event_id=source_event_id, relay_depth=relay_depth)
        except Exception as error:
            self.room.append_agent_error(agent_id, str(error), source_event_id=source_event_id)
        finally:
            with self._lock:
                self._busy.discard(agent_id)


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


def _mentions(content: str) -> set[str]:
    return {match.casefold() for match in re.findall(r"@([A-Za-z0-9_.-]+)", content or "")}


def _configure_slave_terminal(fd: int) -> None:
    if termios is None:
        return
    attrs = termios.tcgetattr(fd)
    attrs[3] = attrs[3] & ~(termios.ECHO | termios.ICANON)
    attrs[6][termios.VMIN] = 1
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def _clean_terminal_text(response: bytes) -> str:
    text = response.decode("utf-8", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"\x1b[@-_][0-?]*[ -/]*[@-~]?", "", text)
    text = text.replace("\x07", "")
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
