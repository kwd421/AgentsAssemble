from __future__ import annotations

import json
import os
import re
import selectors
import signal
import subprocess
import threading
import time
from collections.abc import Callable

try:
    import pty
except ImportError:  # pragma: no cover - depends on host platform
    pty = None  # type: ignore[assignment]

try:
    import termios
except ImportError:  # pragma: no cover - depends on host platform
    termios = None  # type: ignore[assignment]


class JsonlLiveSession:
    def __init__(
        self,
        command: list[str],
        *,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        max_response_bytes: int = 128_000,
        stderr_tail_limit: int = 4_000,
    ) -> None:
        if not command:
            raise ValueError("Live session command is required.")
        self.command = list(command)
        self.max_response_bytes = max(1, int(max_response_bytes))
        self.stderr_tail_limit = max(0, int(stderr_tail_limit))
        self._lock = threading.Lock()
        self._stderr_lock = threading.Lock()
        self._write_selector_lock = threading.Lock()
        self._stderr_tail = b""
        self._stdout_buffer = b""
        self._request_count = 0
        self._closed = False
        self._selector: selectors.BaseSelector | None = None
        self._write_selector: selectors.BaseSelector | None = None
        self._stderr_thread: threading.Thread | None = None
        self.process = popen_factory(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=_supports_process_groups(),
        )
        _remember_process_group(self.process)
        if self.process.stdin is None or self.process.stdout is None:
            self.close()
            raise RuntimeError("Live session process did not expose stdin/stdout.")

        self._selector = selectors.DefaultSelector()
        self._selector.register(self.process.stdout, selectors.EVENT_READ)
        self._stderr_thread = self._start_stderr_drain()

    @property
    def stderr_tail(self) -> str:
        with self._stderr_lock:
            tail = self._stderr_tail
        return tail.decode("utf-8", errors="replace")

    def ask(self, prompt: str, *, timeout_seconds: int | float) -> str:
        with self._lock:
            self._ensure_running()
            self._request_count += 1
            request_id = f"req-{self._request_count}"
            deadline = time.monotonic() + max(0.0, float(timeout_seconds))
            self._write_request({"request_id": request_id, "prompt": prompt}, deadline=deadline, timeout_seconds=timeout_seconds)
            line = self._readline(deadline=deadline, timeout_seconds=timeout_seconds)
            message = _message_from_jsonl(line, expected_request_id=request_id)
            if not message.strip():
                raise ValueError("Live session returned an empty message.")
            return message

    def close(self, *, timeout_seconds: float = 2.0) -> None:
        if self._closed:
            return
        self._closed = True
        self._close_write_selector()
        self._close_selector()
        self._close_stream(self.process.stdin)
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                _terminate_process(self.process, timeout_seconds=timeout_seconds)
        _terminate_process_group_children(self.process, timeout_seconds=timeout_seconds)
        self._join_stderr_thread(timeout_seconds=0.2)
        self._close_stream(self.process.stdout)
        self._close_stream(self.process.stderr)

    def _write_request(
        self,
        payload: dict[str, object],
        *,
        deadline: float,
        timeout_seconds: int | float,
    ) -> None:
        assert self.process.stdin is not None
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        fd = self.process.stdin.fileno()
        previous_blocking = os.get_blocking(fd)
        os.set_blocking(fd, False)
        current_selector: selectors.BaseSelector | None = None
        try:
            with selectors.DefaultSelector() as selector:
                current_selector = selector
                with self._write_selector_lock:
                    self._write_selector = selector
                selector.register(self.process.stdin, selectors.EVENT_WRITE)
                offset = 0
                while offset < len(data):
                    if self._closed:
                        raise RuntimeError("Live session closed while writing request.")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self.close(timeout_seconds=0.1)
                        raise TimeoutError(f"Live session timed out after {timeout_seconds} seconds.")
                    try:
                        events = selector.select(timeout=min(remaining, 0.05))
                    except (OSError, ValueError) as error:
                        raise RuntimeError("Live session closed while writing request.") from error
                    if self._closed:
                        raise RuntimeError("Live session closed while writing request.")
                    if not events:
                        continue
                    try:
                        written = os.write(fd, data[offset:])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError as error:
                        self.close()
                        raise self._process_closed_error() from error
                    if written == 0:
                        self.close()
                        raise RuntimeError("Live session stdin closed while writing request.")
                    offset += written
        finally:
            with self._write_selector_lock:
                if self._write_selector is current_selector:
                    self._write_selector = None
            try:
                os.set_blocking(fd, previous_blocking)
            except OSError:
                pass

    def _readline(self, *, deadline: float, timeout_seconds: int | float) -> bytes:
        assert self.process.stdout is not None
        while True:
            newline_index = self._stdout_buffer.find(b"\n")
            if newline_index >= 0:
                line = self._stdout_buffer[: newline_index + 1]
                self._stdout_buffer = self._stdout_buffer[newline_index + 1 :]
                return line
            if len(self._stdout_buffer) > self.max_response_bytes:
                self.close(timeout_seconds=0.1)
                raise ValueError(f"Live session response exceeded {self.max_response_bytes} bytes.")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.close(timeout_seconds=0.1)
                raise TimeoutError(f"Live session timed out after {timeout_seconds} seconds.")
            selector = self._selector
            if selector is None:
                raise RuntimeError("Live session closed while waiting for response.")
            try:
                events = selector.select(timeout=remaining)
            except (OSError, ValueError) as error:
                raise RuntimeError("Live session closed while waiting for response.") from error
            if not events:
                self.close(timeout_seconds=0.1)
                raise TimeoutError(f"Live session timed out after {timeout_seconds} seconds.")
            try:
                chunk = os.read(self.process.stdout.fileno(), 4096)
            except OSError as error:
                raise RuntimeError("Live session closed while reading response.") from error
            if chunk == b"":
                raise self._process_closed_error()
            self._stdout_buffer += chunk

    def _process_closed_error(self) -> RuntimeError:
        returncode = self.process.poll()
        if returncode is not None:
            self._join_stderr_thread(timeout_seconds=0.2)
        message = f"Live session closed stdout with return code {returncode}."
        stderr_tail = self.stderr_tail.strip()
        if stderr_tail:
            safe_stderr_tail = _stderr_tail_for_error(stderr_tail)
            message = f"{message} {safe_stderr_tail}"
        return RuntimeError(message)

    def _ensure_running(self) -> None:
        if self._closed:
            raise RuntimeError("Live session is closed.")
        returncode = self.process.poll()
        if returncode is not None:
            raise RuntimeError(f"Live session exited with return code {returncode}.")

    def _start_stderr_drain(self) -> threading.Thread | None:
        if self.process.stderr is None:
            return None
        thread = threading.Thread(target=self._drain_stderr, daemon=True)
        thread.start()
        return thread

    def _drain_stderr(self) -> None:
        assert self.process.stderr is not None
        while True:
            try:
                chunk = os.read(self.process.stderr.fileno(), 4096)
            except OSError:
                return
            if chunk == b"":
                return
            if self.stderr_tail_limit <= 0:
                continue
            with self._stderr_lock:
                self._stderr_tail = (self._stderr_tail + chunk)[-self.stderr_tail_limit :]

    def _join_stderr_thread(self, *, timeout_seconds: float) -> None:
        if self._stderr_thread is not None and self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=timeout_seconds)

    def _close_selector(self) -> None:
        if self._selector is None:
            return
        try:
            self._selector.close()
        except Exception:
            pass
        self._selector = None

    def _close_write_selector(self) -> None:
        with self._write_selector_lock:
            selector = self._write_selector
            self._write_selector = None
        if selector is None:
            return
        try:
            selector.close()
        except Exception:
            pass

    @staticmethod
    def _close_stream(stream: object) -> None:
        close = getattr(stream, "close", None)
        if close is None:
            return
        try:
            close()
        except OSError:
            pass


class TerminalLiveSession:
    def __init__(
        self,
        command: list[str],
        *,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        idle_timeout_seconds: float = 0.35,
        max_response_bytes: int = 128_000,
    ) -> None:
        if not command:
            raise ValueError("Terminal session command is required.")
        if not terminal_sessions_supported():
            raise RuntimeError("PTY terminal sessions are not available on this host.")
        self.command = list(command)
        self.idle_timeout_seconds = max(0.01, float(idle_timeout_seconds))
        self.max_response_bytes = max(1, int(max_response_bytes))
        self._lock = threading.Lock()
        self._closed = False
        assert pty is not None
        master_fd, slave_fd = pty.openpty()
        self._master_fd = master_fd
        try:
            _disable_terminal_echo(slave_fd)
            self.process = popen_factory(
                self.command,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                bufsize=0,
                close_fds=True,
                start_new_session=_supports_process_groups(),
            )
        except Exception:
            try:
                os.close(master_fd)
            except OSError:
                pass
            raise
        finally:
            os.close(slave_fd)
        _remember_process_group(self.process)
        os.set_blocking(self._master_fd, False)
        self._drain_available(timeout_seconds=0.05)

    def ask(self, prompt: str, *, timeout_seconds: int | float) -> str:
        with self._lock:
            self._ensure_running()
            self._drain_available(timeout_seconds=0.01)
            deadline = time.monotonic() + max(0.0, float(timeout_seconds))
            self._write_terminal_submission(prompt, deadline=deadline, timeout_seconds=timeout_seconds)
            response = self._read_until_idle(deadline=deadline, timeout_seconds=timeout_seconds)
            message = _clean_terminal_response(response)
            if not message.strip():
                raise ValueError("Terminal session returned an empty message.")
            return message

    def close(self, *, timeout_seconds: float = 2.0) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            os.close(self._master_fd)
        except OSError:
            pass
        if self.process.poll() is None:
            _terminate_process(self.process, timeout_seconds=timeout_seconds)
        _terminate_process_group_children(self.process, timeout_seconds=timeout_seconds)

    def _write_terminal_submission(
        self,
        prompt: str,
        *,
        deadline: float,
        timeout_seconds: int | float,
    ) -> None:
        data = (_single_terminal_submission(prompt) + "\n").encode("utf-8")
        offset = 0
        while offset < len(data):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.close(timeout_seconds=0.1)
                raise TimeoutError(f"Terminal session timed out after {timeout_seconds} seconds.")
            writable = _select_fds([], [self._master_fd], remaining)[1]
            if not writable:
                self.close(timeout_seconds=0.1)
                raise TimeoutError(f"Terminal session timed out after {timeout_seconds} seconds.")
            try:
                written = os.write(self._master_fd, data[offset:])
            except OSError as error:
                self.close(timeout_seconds=0.1)
                raise RuntimeError("Terminal session closed while writing request.") from error
            if written == 0:
                self.close(timeout_seconds=0.1)
                raise RuntimeError("Terminal session closed while writing request.")
            offset += written

    def _read_until_idle(self, *, deadline: float, timeout_seconds: int | float) -> bytes:
        chunks: list[bytes] = []
        total_bytes = 0
        last_read_at: float | None = None
        while True:
            now = time.monotonic()
            if last_read_at is None:
                wait_until = deadline
            else:
                wait_until = min(deadline, last_read_at + self.idle_timeout_seconds)
            wait_seconds = max(0.0, wait_until - now)
            if wait_seconds <= 0:
                if chunks and last_read_at is not None and now >= last_read_at + self.idle_timeout_seconds:
                    return b"".join(chunks)
                self.close(timeout_seconds=0.1)
                raise TimeoutError(f"Terminal session timed out after {timeout_seconds} seconds.")
            readable = _select_fds([self._master_fd], [], wait_seconds)[0]
            if not readable:
                now = time.monotonic()
                if chunks and last_read_at is not None and now >= last_read_at + self.idle_timeout_seconds:
                    return b"".join(chunks)
                self.close(timeout_seconds=0.1)
                raise TimeoutError(f"Terminal session timed out after {timeout_seconds} seconds.")
            chunk = self._read_master_chunk()
            if not chunk:
                raise self._process_closed_error()
            chunks.append(chunk)
            total_bytes += len(chunk)
            last_read_at = time.monotonic()
            if total_bytes > self.max_response_bytes:
                self.close(timeout_seconds=0.1)
                raise ValueError(f"Terminal session response exceeded {self.max_response_bytes} bytes.")

    def _drain_available(self, *, timeout_seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while time.monotonic() < deadline:
            readable = _select_fds([self._master_fd], [], 0)[0]
            if not readable:
                return
            if not self._read_master_chunk():
                return

    def _read_master_chunk(self) -> bytes:
        try:
            return os.read(self._master_fd, 4096)
        except BlockingIOError:
            return b""
        except OSError as error:
            if self.process.poll() is not None:
                return b""
            raise RuntimeError("Terminal session closed while reading response.") from error

    def _ensure_running(self) -> None:
        if self._closed:
            raise RuntimeError("Terminal session is closed.")
        returncode = self.process.poll()
        if returncode is not None:
            raise RuntimeError(f"Terminal session exited with return code {returncode}.")

    def _process_closed_error(self) -> RuntimeError:
        returncode = self.process.poll()
        return RuntimeError(f"Terminal session closed with return code {returncode}.")


def _terminate_process(process: subprocess.Popen[bytes], *, timeout_seconds: float) -> None:
    if process.poll() is not None:
        return
    _send_process_stop_signal(process, _stop_signal("SIGTERM"), force=False)
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _send_process_stop_signal(process, _stop_signal("SIGKILL"), force=True)
        process.wait(timeout=timeout_seconds)


def _terminate_process_group_children(process: subprocess.Popen[bytes], *, timeout_seconds: float) -> None:
    if _process_group_pid(process) is None:
        return
    _send_process_stop_signal(process, _stop_signal("SIGTERM"), force=False)
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while _process_group_exists(process) and time.monotonic() < deadline:
        time.sleep(0.01)
    if _process_group_exists(process):
        _send_process_stop_signal(process, _stop_signal("SIGKILL"), force=True)


def _send_process_stop_signal(process: subprocess.Popen[bytes], stop_signal: int | None, *, force: bool) -> None:
    process_group_pid = _process_group_pid(process)
    if process_group_pid is not None and stop_signal is not None:
        try:
            os.killpg(process_group_pid, stop_signal)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    try:
        if force:
            process.kill()
        else:
            process.terminate()
    except ProcessLookupError:
        return


def _remember_process_group(process: subprocess.Popen[bytes]) -> None:
    if not _supports_process_groups():
        return
    process_group_pid = getattr(process, "pid", None)
    if not isinstance(process_group_pid, int) or process_group_pid <= 0:
        return
    try:
        if os.getpgid(process_group_pid) != process_group_pid:
            return
    except OSError:
        return
    setattr(process, "_agentsassemble_process_group_pid", process_group_pid)


def _process_group_pid(process: subprocess.Popen[bytes]) -> int | None:
    if not _supports_process_groups():
        return None
    pgid = getattr(process, "_agentsassemble_process_group_pid", None)
    return pgid if isinstance(pgid, int) and pgid > 0 else None


def _process_group_exists(process: subprocess.Popen[bytes]) -> bool:
    pgid = _process_group_pid(process)
    if pgid is None:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _supports_process_groups() -> bool:
    return hasattr(os, "killpg") and hasattr(os, "setsid") and hasattr(os, "getpgid")


def terminal_sessions_supported() -> bool:
    return (
        pty is not None
        and termios is not None
        and hasattr(pty, "openpty")
        and hasattr(termios, "tcgetattr")
        and hasattr(termios, "tcsetattr")
    )


def _select_fds(readers: list[int], writers: list[int], timeout: float) -> tuple[list[int], list[int], list[int]]:
    import select

    return select.select(readers, writers, [], timeout)


def _disable_terminal_echo(fd: int) -> None:
    assert termios is not None
    attrs = termios.tcgetattr(fd)
    # Long resident prompts are pasted as one terminal submission. Canonical
    # line buffering can overflow before a child process reads the newline.
    attrs[3] = attrs[3] & ~(termios.ECHO | termios.ICANON)
    attrs[6][termios.VMIN] = 1
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def _single_terminal_submission(prompt: str) -> str:
    return " ".join(str(prompt or "").split())


def _clean_terminal_response(response: bytes) -> str:
    text = response.decode("utf-8", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    return text.strip()


def _stop_signal(name: str) -> int | None:
    value = getattr(signal, name, None)
    return value if isinstance(value, int) else None


def _message_from_jsonl(line: bytes, *, expected_request_id: str) -> str:
    try:
        payload = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Live session returned invalid JSONL.") from error
    if not isinstance(payload, dict):
        raise ValueError("Live session JSONL reply must be an object.")
    request_id = payload.get("request_id")
    if request_id != expected_request_id:
        raise ValueError("Live session JSONL reply must include the matching request_id.")
    message = payload.get("message")
    if not isinstance(message, str):
        raise ValueError("Live session JSONL reply requires a string message field.")
    return message


def _stderr_tail_for_error(stderr_tail: str) -> str:
    if _looks_sensitive_stderr_tail(stderr_tail):
        return "stderr tail redacted."
    return f"stderr tail: {stderr_tail}"


def _looks_sensitive_stderr_tail(stderr_tail: str) -> bool:
    lowered = stderr_tail.casefold()
    markers = (
        "authorization",
        "bearer ",
        "credential",
        "password",
        "secret",
        "token",
        "api-key",
        "apikey",
        "x-api-key",
        "http://",
        "https://",
        "env:",
        ".json",
        ".env",
    )
    if any(marker in lowered for marker in markers):
        return True
    if "\\" in stderr_tail or "/" in stderr_tail or "--" in stderr_tail:
        return True
    if re.search(r"\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)", stderr_tail):
        return True
    return False
