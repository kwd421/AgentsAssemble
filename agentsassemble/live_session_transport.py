from __future__ import annotations

import json
import os
import selectors
import subprocess
import threading
import time
from collections.abc import Callable


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
        )
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
                self.process.terminate()
                try:
                    self.process.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=timeout_seconds)
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
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self.close(timeout_seconds=0.1)
                        raise TimeoutError(f"Live session timed out after {timeout_seconds} seconds.")
                    try:
                        events = selector.select(timeout=remaining)
                    except (OSError, ValueError) as error:
                        raise RuntimeError("Live session closed while writing request.") from error
                    if not events:
                        self.close(timeout_seconds=0.1)
                        raise TimeoutError(f"Live session timed out after {timeout_seconds} seconds.")
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
            message = f"{message} stderr tail: {stderr_tail}"
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
