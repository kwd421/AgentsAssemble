"""ACP stdio transport and JSON-RPC correlation."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import queue
import subprocess
from typing import TextIO


class GrokAcpTransportMixin:
    def _request(
        self,
        method: str,
        params: dict[str, object],
        *,
        timeout_seconds: float,
    ) -> dict[str, object]:
        request_id, response_queue = self._begin_request(method, params)
        try:
            response = response_queue.get(timeout=max(0.1, float(timeout_seconds)))
        except queue.Empty as error:
            raise TimeoutError(
                f"Grok ACP {method} timed out after {timeout_seconds} seconds."
            ) from error
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
        if response.get("_eof"):
            raise RuntimeError(f"Grok ACP exited during {method}.")
        if isinstance(response.get("error"), dict):
            detail = response["error"]
            message = self._provider_error_detail(
                str(detail.get("message") or f"Grok ACP {method} failed.")
            )
            self._last_error = message
            raise RuntimeError(message)
        return (
            dict(response.get("result"))
            if isinstance(response.get("result"), dict)
            else {}
        )

    def _begin_request(
        self,
        method: str,
        params: dict[str, object],
    ) -> tuple[int, queue.Queue[dict[str, object]]]:
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            response_queue: queue.Queue[dict[str, object]] = queue.Queue(maxsize=1)
            self._pending[request_id] = response_queue
        try:
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            )
        except Exception:
            with self._lock:
                self._pending.pop(request_id, None)
            raise
        return request_id, response_queue

    def _send_json(self, message: dict[str, object]) -> None:
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._write_lock:
            with self._lock:
                process = self.process
                stream = process.stdin if process is not None else None
            if process is None or process.poll() is not None or stream is None:
                raise RuntimeError("Grok ACP runtime is not running.")
            try:
                stream.write(payload)
                stream.flush()
            except (BrokenPipeError, OSError) as error:
                raise RuntimeError(
                    "Grok ACP stdin closed while sending a request."
                ) from error

    def _stdout_loop(
        self,
        process: subprocess.Popen[str],
        stream: TextIO,
        notifications: queue.Queue[dict[str, object]],
    ) -> None:
        try:
            try:
                for line in stream:
                    if self._stopping.is_set():
                        break
                    with self._lock:
                        if self.process is not process:
                            break
                    try:
                        message = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(message, dict):
                        continue
                    with self._lock:
                        self._stdout_json_line_count += 1
                    if (
                        message.get("method")
                        in {"fs/read_text_file", "fs/write_text_file"}
                        and "id" in message
                    ):
                        self._reject_unsupported_client_request(message)
                        continue
                    if (
                        message.get("method") == "session/request_permission"
                        and "id" in message
                    ):
                        self._respond_to_permission_request(message)
                        continue
                    request_id = message.get("id")
                    if isinstance(request_id, int) and not message.get("method"):
                        with self._lock:
                            response_queue = self._pending.get(request_id)
                        if response_queue is not None:
                            put_nowait(response_queue, message)
                        continue
                    method = str(message.get("method") or "")
                    if method in {
                        "session/update",
                        "_x.ai/session_notification",
                        "_x.ai/sessions/changed",
                    }:
                        if method == "session/update":
                            self._remember_tool_permission_context(message)
                        self._queue_notification(notifications, message)
            except (OSError, ValueError):
                pass
        finally:
            eof = {"_eof": True}
            with self._lock:
                current_process = self.process is process
                pending = list(self._pending.values()) if current_process else []
            if current_process:
                for response_queue in pending:
                    put_nowait(response_queue, eof)
                self._queue_notification(notifications, eof)

    def _stderr_loop(self, stream: TextIO) -> None:
        try:
            for line in stream:
                encoded = line.encode("utf-8", errors="replace")
                self._record_stderr_line(
                    line.rstrip("\r\n"),
                    byte_count=len(encoded),
                )
        except (OSError, ValueError):
            pass

    def _record_stderr_line(self, line: str, *, byte_count: int) -> None:
        with self._lock:
            self._stderr_byte_count += max(0, int(byte_count))
            self._stderr_line_count += 1
            self._stderr_last_line_at = datetime.now(UTC).isoformat()
            if "warn" in line.casefold() or "warning" in line.casefold():
                self._stderr_warning_count += 1
            if len(self._stderr_tail) == self._stderr_tail.maxlen:
                self._stderr_tail_truncated = True
            bounded_line = line[-16_000:]
            if bounded_line != line:
                self._stderr_tail_truncated = True
            self._stderr_tail.append(bounded_line)
            while len("\n".join(self._stderr_tail)) > 16_000:
                self._stderr_tail_truncated = True
                if len(self._stderr_tail) == 1:
                    self._stderr_tail[0] = self._stderr_tail[0][-16_000:]
                    break
                self._stderr_tail.popleft()


def put_nowait(
    target: queue.Queue[dict[str, object]],
    value: dict[str, object],
) -> None:
    try:
        target.put_nowait(value)
    except queue.Full:
        try:
            target.get_nowait()
        except queue.Empty:
            pass
        try:
            target.put_nowait(value)
        except queue.Full:
            pass


__all__ = ["GrokAcpTransportMixin", "put_nowait"]
