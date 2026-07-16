from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from agentsassemble.live_cli_output import extract_live_cli_terminal_message, terminal_text_contains
from agentsassemble.live_cli_transcripts import LiveCliMessageSource, make_live_cli_message_source
from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.providers.process_environment import sanitized_provider_environment


class WindowsConPtyRuntime:
    """Windows ConPTY equivalent of LiveCliRuntime, backed by pywinpty."""

    def __init__(
        self,
        agent_id: str,
        command: list[str],
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        idle_quiet_seconds: float = 0.35,
        submit_newline: str = "\r",
        submit_delay_seconds: float = 0.1,
        input_mode: str = "line",
        terminal_rows: int = 40,
        terminal_columns: int = 120,
        max_output_bytes: int = 256_000,
        message_source: LiveCliMessageSource | None = None,
        process_factory=None,
        profile_settings: dict[str, object] | None = None,
        startup_quiet_seconds: float = 0.0,
        startup_timeout_seconds: float = 0.0,
        startup_accept_contains: str = "",
        startup_accept_keys: str = "\r",
        startup_ready_contains: str = "",
        startup_input: str = "",
    ) -> None:
        if not command:
            raise ValueError("ConPTY command is required.")
        self.agent_id = clean_lobby_text(agent_id, limit=128)
        self.command = list(command)
        self.cwd = Path(cwd).expanduser().resolve() if cwd else Path.cwd().resolve()
        self.env = dict(env or {})
        self.idle_quiet_seconds = max(0.01, float(idle_quiet_seconds))
        self.submit_newline = submit_newline or "\r"
        self.submit_delay_seconds = max(0.0, float(submit_delay_seconds))
        self.startup_quiet_seconds = max(0.0, float(startup_quiet_seconds))
        self.startup_timeout_seconds = max(0.0, float(startup_timeout_seconds))
        self.startup_accept_contains = str(startup_accept_contains or "")
        self.startup_accept_keys = str(startup_accept_keys or "\r")
        self.startup_ready_contains = str(startup_ready_contains or "")
        self.startup_input = str(startup_input or "")
        self.input_mode = clean_lobby_text(input_mode, limit=64) or "line"
        self.terminal_rows = max(10, int(terminal_rows))
        self.terminal_columns = max(40, int(terminal_columns))
        self.max_output_bytes = max(1, int(max_output_bytes))
        self._process_factory = process_factory or _spawn_winpty
        self._message_source = message_source or make_live_cli_message_source(
            self.agent_id, self.command, cwd=self.cwd
        )
        self.profile_settings = {
            key: clean_lobby_text(value, limit=256)
            for key, value in dict(profile_settings or {}).items()
            if clean_lobby_text(value, limit=256)
        }
        self.process = None
        self._reader: threading.Thread | None = None
        self._output = bytearray()
        self._turn_start = 0
        self._last_read_at = 0.0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._started_at = ""
        self._last_error = ""
        self._startup_drained = False
        self._startup_input_sent = False

    def start(self) -> dict[str, object]:
        with self._lock:
            if self._alive():
                return self.health()
            executable = shutil.which(self.command[0])
            if not executable:
                raise FileNotFoundError(f"configured command missing: {self.command[0]}")
            self._message_source.prepare_start()
            process = self._process_factory(
                [executable, *self.command[1:]],
                cwd=str(self.cwd),
                env=sanitized_provider_environment(self.env),
                rows=self.terminal_rows,
                columns=self.terminal_columns,
            )
            self.process = process
            self._stop.clear()
            self._output = bytearray()
            self._started_at = _now()
            self._last_error = ""
            self._startup_drained = False
            self._startup_input_sent = False
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
        return self.health()

    def send(self, text: str) -> None:
        self.start()
        self._drain_startup_output()
        if self.startup_input and not self._startup_input_sent:
            self.process.write(self.startup_input)
            self._startup_input_sent = True
            time.sleep(max(0.2, self.startup_quiet_seconds))
        with self._lock:
            self._turn_start = len(self._output)
            process = self.process
        self._message_source.begin_turn(str(text or ""))
        payload = str(text or "")
        if self.input_mode == "bracketed_paste":
            process.write(f"\x1b[200~{payload}\x1b[201~{self.submit_newline}")
        else:
            process.write(payload + ("" if payload.endswith(self.submit_newline) else self.submit_newline))

    def read_output(
        self,
        *,
        timeout_seconds: float,
        on_delta: Callable[[str], None] | None = None,
        on_activity: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        del on_activity
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        previous = ""
        while time.monotonic() < deadline:
            with self._lock:
                response = bytes(self._output[self._turn_start :])
                last_read_at = self._last_read_at
                alive = self._alive()
            if len(response) > self.max_output_bytes:
                raise ValueError(f"ConPTY output exceeded {self.max_output_bytes} bytes.")
            quiet = bool(response and time.monotonic() - last_read_at >= self.idle_quiet_seconds)
            snapshot = self._message_source.poll(response, quiet=quiet)
            if snapshot.error:
                raise RuntimeError(snapshot.error)
            if snapshot.content:
                delta = snapshot.content[len(previous) :] if snapshot.content.startswith(previous) else snapshot.content
                if delta and on_delta is not None:
                    on_delta(delta)
                previous = snapshot.content
            if snapshot.complete:
                return {
                    "outcome": "message",
                    "actor_id": self.agent_id,
                    "actor_type": "agent",
                    "kind": "agent_message",
                    "content": snapshot.content,
                    "metadata": {"message_source": snapshot.source_kind},
                }
            if quiet and not getattr(self._message_source, "strict", False):
                content = extract_live_cli_terminal_message(response)
                if content:
                    if on_delta is not None and content.startswith(previous):
                        remainder = content[len(previous) :]
                        if remainder:
                            on_delta(remainder)
                    return {
                        "outcome": "message",
                        "actor_id": self.agent_id,
                        "actor_type": "agent",
                        "kind": "agent_message",
                        "content": content,
                        "metadata": {"message_source": "conpty"},
                    }
            if not alive:
                raise RuntimeError("ConPTY provider process exited before completing the turn.")
            time.sleep(0.02)
        raise TimeoutError(f"ConPTY runtime timed out after {timeout_seconds} seconds.")

    def interrupt(self) -> None:
        with self._lock:
            process = self.process
        if process is not None:
            process.write("\x03")

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        del timeout_seconds
        self._stop.set()
        with self._lock:
            process = self.process
            self.process = None
        if process is not None:
            for name in ("terminate", "close"):
                method = getattr(process, name, None)
                if callable(method):
                    try:
                        method()
                    except Exception:
                        pass
                    break
        reader = self._reader
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.0)

    def health(self) -> dict[str, object]:
        with self._lock:
            process = self.process
            running = self._alive()
            return {
                "agent_id": self.agent_id,
                "runtime_kind": "live_cli",
                "running": running,
                "transport": "conpty",
                "provider_session_active": running,
                "pty": True,
                "is_one_shot": False,
                "pid": getattr(process, "pid", None),
                "started_at": self._started_at,
                "last_error": self._last_error,
                "startup_accept_configured": bool(self.startup_accept_contains),
                "startup_ready_configured": bool(self.startup_ready_contains),
                "terminal_byte_count": len(self._output),
                "terminal_tail": bytes(self._output[-16_000:]).decode("utf-8", errors="replace"),
                **self.profile_settings,
                **self._message_source.describe(),
            }

    def _drain_startup_output(self) -> None:
        if self._startup_drained:
            return
        if self.startup_timeout_seconds <= 0:
            if self.startup_ready_contains:
                raise TimeoutError(
                    f"{self.agent_id} did not expose its configured startup readiness marker."
                )
            self._startup_drained = True
            return
        deadline = time.monotonic() + self.startup_timeout_seconds
        accepted = False
        ready = not bool(self.startup_ready_contains)
        while time.monotonic() < deadline:
            with self._lock:
                process = self.process
                output = bytes(self._output)
                last_read_at = self._last_read_at
            if process is None or not self._alive():
                raise RuntimeError("ConPTY provider process exited during startup.")
            if (
                not accepted
                and self.startup_accept_contains
                and terminal_text_contains(output, self.startup_accept_contains)
            ):
                process.write(self.startup_accept_keys)
                accepted = True
            if not ready and terminal_text_contains(output, self.startup_ready_contains):
                ready = True
            if ready and last_read_at and (
                self.startup_quiet_seconds <= 0
                or time.monotonic() - last_read_at >= self.startup_quiet_seconds
            ):
                break
            time.sleep(0.02)
        if not ready:
            raise TimeoutError(
                f"{self.agent_id} did not expose its configured startup readiness marker."
            )
        self._startup_drained = True

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                process = self.process
            if process is None or not self._alive():
                return
            try:
                chunk = process.read(4096)
            except (EOFError, OSError):
                return
            except Exception as error:
                with self._lock:
                    self._last_error = type(error).__name__
                return
            if not chunk:
                time.sleep(0.01)
                continue
            data = chunk.encode("utf-8", errors="replace") if isinstance(chunk, str) else bytes(chunk)
            with self._lock:
                self._output.extend(data)
                if len(self._output) > self.max_output_bytes * 2:
                    removed = len(self._output) - self.max_output_bytes
                    del self._output[:removed]
                    self._turn_start = max(0, self._turn_start - removed)
                self._last_read_at = time.monotonic()

    def _alive(self) -> bool:
        process = self.process
        if process is None:
            return False
        checker = getattr(process, "isalive", None)
        if callable(checker):
            return bool(checker())
        return not bool(getattr(process, "closed", False))


def _spawn_winpty(command: list[str], *, cwd: str, env: dict[str, str], rows: int, columns: int):
    try:
        from winpty import PtyProcess
    except ImportError as error:  # pragma: no cover - Windows dependency gate
        raise RuntimeError("pywinpty is required for Windows Agent Sessions.") from error
    return PtyProcess.spawn(command, cwd=cwd, env=env, dimensions=(rows, columns))


def _now() -> str:
    return datetime.now(UTC).isoformat()
