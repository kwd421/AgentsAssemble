from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from agentsassemble.providers.process_environment import sanitized_provider_environment
from agentsassemble.providers.room_portal import VIRTUAL_ROOM_OUTBOX_PATH
from agentsassemble.providers.runtime_contracts import AdapterContractError
from agentsassemble.room.text import clean_room_text

if TYPE_CHECKING:
    from agentsassemble.providers.room_portal import RoomPortal


class GrokAcpRuntime:
    """Persistent Grok CLI runtime using its structured ACP stdio transport."""

    def __init__(
        self,
        agent_id: str,
        command: list[str],
        *,
        cwd: str | Path,
        state_dir: str | Path,
        env: dict[str, str] | None = None,
        auth_path: str | Path | None = None,
        room_portal: RoomPortal | None = None,
        popen_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
        startup_timeout_seconds: float = 20.0,
        notification_queue_size: int = 20_000,
    ) -> None:
        if not command:
            raise ValueError("Grok ACP command is required.")
        self.agent_id = clean_room_text(agent_id, limit=128)
        self.command = list(command)
        self.cwd = Path(cwd).expanduser().resolve()
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.env = dict(env or {})
        self.room_portal = room_portal
        selected_auth_path = auth_path or self.env.get("GROK_AUTH_PATH") or os.environ.get("GROK_AUTH_PATH")
        self.auth_path = (
            Path(selected_auth_path).expanduser().resolve()
            if selected_auth_path
            else Path.home() / ".grok" / "auth.json"
        )
        self._popen_factory = popen_factory
        self.startup_timeout_seconds = max(1.0, float(startup_timeout_seconds))

        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._request_id = 0
        self._pending: dict[int, queue.Queue[dict[str, object]]] = {}
        self._notification_queue_size = max(1, int(notification_queue_size))
        self._notifications: queue.Queue[dict[str, object]] = queue.Queue(
            maxsize=self._notification_queue_size
        )
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self.process: subprocess.Popen[str] | None = None
        self._session_id = ""
        self._active_request: tuple[int, queue.Queue[dict[str, object]]] | None = None
        self._active_room_observation = False
        self._resolved_executable = ""
        self._started_at = ""
        self._stopped_at = ""
        self._last_error = ""
        self._model = ""
        self._supports_load_session = False
        self._provider_session_reused = False
        self._provider_session_resume_failed = False
        self._provider_session_resume_error = ""
        self._yolo_mode: bool | None = None
        self._stdout_json_line_count = 0
        self._notification_drop_count = 0
        self._turn_notification_drop_start = 0
        self._permission_request_count = 0
        self._permission_denied_count = 0
        self._tool_permission_context: dict[
            tuple[str, str],
            dict[str, object],
        ] = {}
        self._active_thought_text = ""
        self._last_emitted_thought_text = ""
        self._tool_activity_state: dict[str, tuple[str, str]] = {}
        self._stderr_byte_count = 0
        self._stderr_line_count = 0
        self._stderr_warning_count = 0
        self._stderr_last_line_at = ""
        self._stderr_tail: deque[str] = deque(maxlen=100)
        self._stderr_tail_truncated = False

    def start(self) -> dict[str, object]:
        with self._lock:
            if self._running_locked():
                return self.health()
            stale_process = self.process
            if stale_process is not None:
                for stream in (stale_process.stdin, stale_process.stdout, stale_process.stderr):
                    _close_stream(stream)
            self._resolved_executable = _resolve_executable(self.command[0])
            if not self._resolved_executable:
                self._last_error = f"configured command missing: {self.command[0]}"
                raise FileNotFoundError(self._last_error)
            self.state_dir.mkdir(parents=True, exist_ok=True)
            try:
                self.state_dir.chmod(0o700)
            except OSError:
                pass
            process_env = sanitized_provider_environment(self.env)
            process_env.update(
                {
                    "GROK_HOME": str(self.state_dir),
                    "GROK_AUTH_PATH": str(self.auth_path),
                    "GROK_DEFAULT_SELECTED_PERMISSION": "reject",
                }
            )
            process = self._popen_factory(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=str(self.cwd),
                env=process_env,
                start_new_session=True,
            )
            if process.stdin is None or process.stdout is None or process.stderr is None:
                process.terminate()
                raise RuntimeError("Grok ACP process did not expose stdio pipes.")
            self.process = process
            self._stopping.clear()
            self._session_id = ""
            self._active_request = None
            self._active_room_observation = False
            self._tool_permission_context.clear()
            self._active_thought_text = ""
            self._last_emitted_thought_text = ""
            self._tool_activity_state.clear()
            self._pending.clear()
            process_notifications: queue.Queue[dict[str, object]] = queue.Queue(
                maxsize=self._notification_queue_size
            )
            self._notifications = process_notifications
            self._started_at = _now()
            self._stopped_at = ""
            self._last_error = ""
            self._provider_session_reused = False
            self._provider_session_resume_failed = False
            self._provider_session_resume_error = ""
            self._yolo_mode = None
            self._stdout_thread = threading.Thread(
                target=self._stdout_loop,
                args=(process, process.stdout, process_notifications),
                name=f"GrokAcpStdout-{self.agent_id}",
                daemon=True,
            )
            self._stderr_thread = threading.Thread(
                target=self._stderr_loop,
                args=(process.stderr,),
                name=f"GrokAcpStderr-{self.agent_id}",
                daemon=True,
            )
            self._stdout_thread.start()
            self._stderr_thread.start()

        try:
            initialized = self._request(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {
                        "fs": {
                            "readTextFile": self.room_portal is not None,
                            "writeTextFile": self.room_portal is not None,
                        },
                        "terminal": False,
                    },
                    "clientInfo": {"name": "AgentsAssemble", "version": "0"},
                },
                timeout_seconds=self.startup_timeout_seconds,
            )
            meta = initialized.get("_meta") if isinstance(initialized.get("_meta"), dict) else {}
            model_state = meta.get("modelState") if isinstance(meta.get("modelState"), dict) else {}
            self._model = clean_room_text(model_state.get("currentModelId"), limit=128)
            capabilities = (
                initialized.get("agentCapabilities")
                if isinstance(initialized.get("agentCapabilities"), dict)
                else {}
            )
            self._supports_load_session = bool(capabilities.get("loadSession", False))
            session_id = self._resume_provider_session()
            if not session_id:
                created = self._request(
                    "session/new",
                    {"cwd": str(self.cwd), "mcpServers": []},
                    timeout_seconds=self.startup_timeout_seconds,
                )
                session_id = clean_room_text(created.get("sessionId"), limit=128)
            if not session_id:
                raise RuntimeError("Grok ACP did not return a provider session id.")
            self._persist_provider_session(session_id)
            with self._lock:
                self._session_id = session_id
                self._last_error = ""
            return self.health()
        except Exception as error:
            self._last_error = str(error)
            self.stop(timeout_seconds=2.0)
            raise

    def send(self, text: str) -> None:
        self._begin_prompt(
            [{"type": "text", "text": str(text or "")}],
            room_observation=False,
        )

    def send_room_observation(
        self,
        text: str,
        *,
        media_blocks: list[dict[str, str]] | None = None,
    ) -> None:
        prompt = [{"type": "text", "text": str(text or "")}]
        prompt.extend(dict(block) for block in list(media_blocks or []))
        self._begin_prompt(prompt, room_observation=True)

    def _begin_prompt(
        self,
        prompt: list[dict[str, str]],
        *,
        room_observation: bool,
    ) -> None:
        self.start()
        with self._lock:
            if self._active_request is not None:
                raise RuntimeError("Grok ACP runtime already has an active turn.")
            session_id = self._session_id
        self._consume_notifications(session_id, [], on_delta=None, on_activity=None)
        with self._lock:
            self._turn_notification_drop_start = self._notification_drop_count
            self._active_room_observation = room_observation
            self._tool_permission_context.clear()
            self._active_thought_text = ""
            self._last_emitted_thought_text = ""
            self._tool_activity_state.clear()
        try:
            request = self._begin_request(
                "session/prompt",
                {
                    "sessionId": session_id,
                    "prompt": prompt,
                },
            )
        except Exception:
            with self._lock:
                self._active_room_observation = False
            raise
        with self._lock:
            self._active_request = request

    def read_output(
        self,
        *,
        timeout_seconds: float,
        on_delta: Callable[[str], None] | None = None,
        on_activity: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        with self._lock:
            active = self._active_request
            room_observation = self._active_room_observation
            session_id = self._session_id
            notification_drop_start = self._turn_notification_drop_start
        if active is None:
            raise RuntimeError("Grok ACP runtime has no active turn.")
        request_id, response_queue = active
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        content_parts: list[str] = []
        response: dict[str, object] | None = None
        try:
            while time.monotonic() < deadline:
                self._consume_notifications(
                    session_id,
                    content_parts,
                    on_delta=on_delta,
                    on_activity=on_activity,
                )
                self._raise_if_notifications_dropped(notification_drop_start)
                try:
                    response = response_queue.get(timeout=0.05)
                except queue.Empty:
                    response = None
                if response is None:
                    self._raise_if_exited()
                    continue
                self._consume_notifications(
                    session_id,
                    content_parts,
                    on_delta=on_delta,
                    on_activity=on_activity,
                )
                self._raise_if_notifications_dropped(notification_drop_start)
                if response.get("_eof"):
                    raise RuntimeError("Grok ACP runtime exited before turn completion.")
                if isinstance(response.get("error"), dict):
                    error = response["error"]
                    detail = self._provider_error_detail(str(error.get("message") or "Grok ACP turn failed."))
                    self._last_error = detail
                    raise RuntimeError(detail)
                result = response.get("result") if isinstance(response.get("result"), dict) else {}
                result_meta = result.get("_meta") if isinstance(result.get("_meta"), dict) else {}
                model = clean_room_text(result_meta.get("modelId"), limit=128)
                if model:
                    self._model = model
                content = "".join(content_parts).strip()
                if not content:
                    if room_observation:
                        return {
                            "outcome": "decline",
                            "reason_code": "nothing_useful_to_add",
                            "metadata": {
                                "message_source": "grok_acp",
                                "source_kind": "grok_acp",
                                "stop_reason": result.get("stopReason") or "",
                                "observed_model_id": self._model,
                            },
                        }
                    raise AdapterContractError(
                        "Grok ACP completed without a room-visible assistant message.",
                        code="empty_provider_final",
                    )
                return {
                    "outcome": "message",
                    "actor_id": self.agent_id,
                    "actor_type": "agent",
                    "kind": "agent_message",
                    "content": content,
                    "metadata": {
                        "message_source": "grok_acp",
                        "source_kind": "grok_acp",
                        "stop_reason": result.get("stopReason") or "",
                        "observed_model_id": self._model,
                    },
                }
            raise TimeoutError(f"Grok ACP runtime timed out after {timeout_seconds} seconds.")
        finally:
            with self._lock:
                if self._active_request and self._active_request[0] == request_id:
                    self._active_request = None
                self._active_room_observation = False
                self._tool_permission_context.clear()
                self._active_thought_text = ""
                self._last_emitted_thought_text = ""
                self._tool_activity_state.clear()
                self._pending.pop(request_id, None)

    def interrupt(self) -> None:
        with self._lock:
            session_id = self._session_id
            active = self._active_request
        if not session_id or active is None:
            return
        self._request("session/cancel", {"sessionId": session_id}, timeout_seconds=5.0)

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        with self._lock:
            process = self.process
            self.process = None
            self._stopping.set()
        if process is not None:
            _terminate_process(process, timeout_seconds=max(0.1, float(timeout_seconds)))
            for stream in (process.stdin, process.stdout, process.stderr):
                _close_stream(stream)
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=0.5)
        with self._lock:
            self._active_request = None
            self._active_room_observation = False
            self._pending.clear()
            self._session_id = ""
            self._stopped_at = _now()

    def health(self) -> dict[str, object]:
        with self._lock:
            process = self.process
            running = self._running_locked()
            stderr_tail = "\n".join(self._stderr_tail)
            if len(stderr_tail) > 16_000:
                stderr_tail = stderr_tail[-16_000:]
            returncode = process.poll() if process is not None else None
            return {
                "agent_id": self.agent_id,
                "runtime_kind": "live_cli",
                "command_configured": list(self.command),
                "command_display": " ".join(self.command),
                "resolved_executable": self._resolved_executable or _resolve_executable(self.command[0]),
                "cwd": str(self.cwd),
                "workspace_dir": str(self.cwd),
                "session_dir": str(self.state_dir),
                "pty": False,
                "transport": "acp_stdio",
                "is_one_shot": False,
                "message_source": "grok_acp",
                "message_source_strict": True,
                "running": running,
                "stopped": not running,
                "pid": process.pid if process is not None else None,
                "returncode": returncode,
                "started_at": self._started_at,
                "stopped_at": self._stopped_at,
                "last_error": self._last_error,
                "provider_session_active": bool(self._session_id),
                "provider_session_load_supported": self._supports_load_session,
                "provider_session_reused": self._provider_session_reused,
                "provider_session_resume_failed": self._provider_session_resume_failed,
                "provider_session_resume_error": self._provider_session_resume_error,
                "room_portal_supported": self.room_portal is not None,
                "model": self._model,
                "approval_policy": "deny_without_room_approval",
                "yolo_mode": self._yolo_mode,
                "stdout_json_line_count": self._stdout_json_line_count,
                "notification_drop_count": self._notification_drop_count,
                "permission_request_count": self._permission_request_count,
                "permission_denied_count": self._permission_denied_count,
                "stderr_drained": True,
                "stderr_byte_count": self._stderr_byte_count,
                "stderr_line_count": self._stderr_line_count,
                "stderr_warning_count": self._stderr_warning_count,
                "stderr_tail": stderr_tail,
                "stderr_tail_truncated": self._stderr_tail_truncated,
                "stderr_last_line_at": self._stderr_last_line_at,
                "terminal_byte_count": 0,
                "terminal_tail": "",
            }

    def _resume_provider_session(self) -> str:
        stored_session_id = self._read_provider_session()
        if not stored_session_id:
            return ""
        if not self._supports_load_session:
            self._provider_session_resume_failed = True
            self._provider_session_resume_error = "Grok ACP does not support session/load."
            return ""
        try:
            self._request(
                "session/load",
                {
                    "sessionId": stored_session_id,
                    "cwd": str(self.cwd),
                    "mcpServers": [],
                },
                timeout_seconds=self.startup_timeout_seconds,
            )
        except Exception as error:
            self._provider_session_resume_failed = True
            self._provider_session_resume_error = clean_room_text(error, limit=1000)
            return ""
        self._provider_session_reused = True
        return stored_session_id

    @property
    def _session_state_path(self) -> Path:
        return self.state_dir / "agentsassemble-session.json"

    def _read_provider_session(self) -> str:
        try:
            payload = json.loads(self._session_state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
            return ""
        if not isinstance(payload, dict) or payload.get("cwd") != str(self.cwd):
            return ""
        return clean_room_text(payload.get("session_id"), limit=128)

    def _persist_provider_session(self, session_id: str) -> None:
        payload = {
            "version": 1,
            "transport": "grok_acp",
            "session_id": session_id,
            "cwd": str(self.cwd),
            "updated_at": _now(),
        }
        state_path = self._session_state_path
        temporary_path = state_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            temporary_path.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary_path, state_path)

    def _request(self, method: str, params: dict[str, object], *, timeout_seconds: float) -> dict[str, object]:
        request_id, response_queue = self._begin_request(method, params)
        try:
            response = response_queue.get(timeout=max(0.1, float(timeout_seconds)))
        except queue.Empty as error:
            raise TimeoutError(f"Grok ACP {method} timed out after {timeout_seconds} seconds.") from error
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
        if response.get("_eof"):
            raise RuntimeError(f"Grok ACP exited during {method}.")
        if isinstance(response.get("error"), dict):
            detail = response["error"]
            message = self._provider_error_detail(str(detail.get("message") or f"Grok ACP {method} failed."))
            self._last_error = message
            raise RuntimeError(message)
        return dict(response.get("result")) if isinstance(response.get("result"), dict) else {}

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
            self._send_json({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
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
                raise RuntimeError("Grok ACP stdin closed while sending a request.") from error

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
                    if message.get("method") in {"fs/read_text_file", "fs/write_text_file"} and "id" in message:
                        self._handle_room_portal_request(message)
                        continue
                    if message.get("method") == "session/request_permission" and "id" in message:
                        self._respond_to_permission_request(message)
                        continue
                    request_id = message.get("id")
                    if isinstance(request_id, int) and not message.get("method"):
                        with self._lock:
                            response_queue = self._pending.get(request_id)
                        if response_queue is not None:
                            _put_nowait(response_queue, message)
                        continue
                    method = str(message.get("method") or "")
                    if method in {"session/update", "_x.ai/session_notification", "_x.ai/sessions/changed"}:
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
                    _put_nowait(response_queue, eof)
                self._queue_notification(notifications, eof)

    def _stderr_loop(self, stream: TextIO) -> None:
        try:
            for line in stream:
                encoded = line.encode("utf-8", errors="replace")
                self._record_stderr_line(line.rstrip("\r\n"), byte_count=len(encoded))
        except (OSError, ValueError):
            pass

    def _record_stderr_line(self, line: str, *, byte_count: int) -> None:
        with self._lock:
            self._stderr_byte_count += max(0, int(byte_count))
            self._stderr_line_count += 1
            self._stderr_last_line_at = _now()
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

    def _respond_to_permission_request(self, message: dict[str, object]) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
            return
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        tool_call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
        allow_outbox = self._permission_is_room_outbox_write(params, tool_call)
        if allow_outbox:
            allow_outbox = self._stage_room_outbox_write(params, tool_call)
        option_kind = "allow_once" if allow_outbox else "reject_once"
        option_id = ""
        for option in list(params.get("options") or []):
            if not isinstance(option, dict) or str(option.get("kind") or "") != option_kind:
                continue
            option_id = clean_room_text(option.get("optionId"), limit=128)
            if option_id:
                break
        with self._lock:
            self._permission_request_count += 1
            if not allow_outbox:
                self._permission_denied_count += 1
        outcome: dict[str, object]
        if option_id:
            outcome = {"outcome": "selected", "optionId": option_id}
        else:
            outcome = {"outcome": "cancelled"}
        try:
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"outcome": outcome},
                }
            )
        except RuntimeError as error:
            self._last_error = str(error)

    def _permission_is_room_outbox_write(
        self,
        params: dict[str, object],
        tool_call: dict[str, object],
    ) -> bool:
        with self._lock:
            active_room_observation = self._active_room_observation
            session_id = self._session_id
        if (
            not active_room_observation
            or self.room_portal is None
            or str(params.get("sessionId") or "") != session_id
        ):
            return False
        raw_input = (
            tool_call.get("rawInput")
            if isinstance(tool_call.get("rawInput"), dict)
            else {}
        )
        tool_call_id = clean_room_text(
            tool_call.get("toolCallId") or params.get("toolCallId"),
            limit=128,
        )
        with self._lock:
            cached = dict(
                self._tool_permission_context.get((session_id, tool_call_id)) or {}
            )
        cached_input = (
            cached.get("rawInput")
            if isinstance(cached.get("rawInput"), dict)
            else {}
        )
        if raw_input.get("command") or cached_input.get("command"):
            return False
        identity = " ".join(
            clean_room_text(value, limit=120)
            for value in (
                tool_call.get("name"),
                tool_call.get("title"),
                cached.get("name"),
                cached.get("title"),
                cached.get("label"),
            )
            if value
        ).casefold()
        if not any(
            word in identity
            for word in ("write", "write_file", "write text", "fs/write_text_file")
        ):
            return False
        targets: list[str] = []
        for values in (tool_call, raw_input, cached, cached_input):
            for key in ("file_path", "path", "target_file"):
                value = values.get(key)
                if isinstance(value, str) and value:
                    targets.append(value)
        for values in (tool_call, cached):
            for key in ("location", "locations"):
                locations = values.get(key)
                if isinstance(locations, dict):
                    locations = [locations]
                if isinstance(locations, list):
                    targets.extend(
                        str(location.get("path") or "")
                        for location in locations
                        if isinstance(location, dict) and location.get("path")
                    )
        return bool(targets) and all(
            target == VIRTUAL_ROOM_OUTBOX_PATH
            for target in targets
        )

    def _stage_room_outbox_write(
        self,
        params: dict[str, object],
        tool_call: dict[str, object],
    ) -> bool:
        portal = self.room_portal
        if portal is None:
            return False
        session_id = clean_room_text(params.get("sessionId"), limit=128)
        tool_call_id = clean_room_text(
            tool_call.get("toolCallId") or params.get("toolCallId"),
            limit=128,
        )
        raw_input = (
            tool_call.get("rawInput")
            if isinstance(tool_call.get("rawInput"), dict)
            else {}
        )
        with self._lock:
            cached = dict(
                self._tool_permission_context.get((session_id, tool_call_id)) or {}
            )
        cached_input = (
            cached.get("rawInput")
            if isinstance(cached.get("rawInput"), dict)
            else {}
        )
        content = raw_input.get("content") or cached_input.get("content")
        if not isinstance(content, str) or not content.strip():
            return False
        try:
            portal.acp_write_text(VIRTUAL_ROOM_OUTBOX_PATH, content)
        except Exception as error:
            self._last_error = clean_room_text(error, limit=1000)
            return False
        return True

    def _remember_tool_permission_context(self, message: dict[str, object]) -> None:
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        update = params.get("update") if isinstance(params.get("update"), dict) else {}
        if str(update.get("sessionUpdate") or "") not in {"tool_call", "tool_call_update"}:
            return
        tool_call_id = clean_room_text(
            update.get("toolCallId") or update.get("tool_call_id"),
            limit=128,
        )
        if not tool_call_id:
            return
        update_session_id = clean_room_text(params.get("sessionId"), limit=128)
        with self._lock:
            active_session_id = self._session_id
            active_room_observation = self._active_room_observation
        if (
            not active_room_observation
            or not update_session_id
            or update_session_id != active_session_id
        ):
            return
        metadata = update.get("_meta") if isinstance(update.get("_meta"), dict) else {}
        tool_metadata = (
            metadata.get("x.ai/tool")
            if isinstance(metadata.get("x.ai/tool"), dict)
            else {}
        )
        raw_input = update.get("rawInput") if isinstance(update.get("rawInput"), dict) else {}
        locations: list[dict[str, str]] = []
        for key in ("location", "locations"):
            values = update.get(key)
            if isinstance(values, dict):
                values = [values]
            if isinstance(values, list):
                locations.extend(
                    {"path": str(value.get("path"))}
                    for value in values
                    if isinstance(value, dict) and value.get("path")
                )
        incoming = {
            "name": clean_room_text(
                tool_metadata.get("name") or update.get("name"),
                limit=120,
            ),
            "label": clean_room_text(tool_metadata.get("label"), limit=120),
            "title": clean_room_text(update.get("title"), limit=300),
            "rawInput": {
                key: (
                    str(raw_input.get(key))[:12000]
                    if key == "content"
                    else clean_room_text(raw_input.get(key), limit=600)
                )
                for key in ("command", "content", "file_path", "path", "target_file")
                if isinstance(raw_input.get(key), str) and raw_input.get(key)
            },
            "locations": locations,
            **{
                key: clean_room_text(update.get(key), limit=600)
                for key in ("file_path", "path", "target_file")
                if isinstance(update.get(key), str) and update.get(key)
            },
        }
        context_key = (update_session_id, tool_call_id)
        with self._lock:
            previous = dict(self._tool_permission_context.get(context_key) or {})
            previous_input = (
                dict(previous.get("rawInput"))
                if isinstance(previous.get("rawInput"), dict)
                else {}
            )
            previous_input.update(incoming["rawInput"])
            for key in ("name", "label", "title"):
                if incoming.get(key):
                    previous[key] = incoming[key]
            for key in ("file_path", "path", "target_file"):
                if incoming.get(key):
                    previous[key] = incoming[key]
            if incoming["locations"]:
                previous["locations"] = incoming["locations"]
            previous["rawInput"] = previous_input
            self._tool_permission_context[context_key] = previous
            while len(self._tool_permission_context) > 256:
                self._tool_permission_context.pop(next(iter(self._tool_permission_context)))

    def _handle_room_portal_request(self, message: dict[str, object]) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
            return
        portal = self.room_portal
        if portal is None:
            self._send_acp_error(request_id, -32601, "Room portal filesystem is unavailable.")
            return
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        with self._lock:
            session_id = self._session_id
        if str(params.get("sessionId") or "") != session_id:
            self._send_acp_error(request_id, -32602, "Session id does not match.")
            return
        try:
            if method == "fs/read_text_file":
                result = {
                    "content": portal.acp_read_text(
                        params.get("path"),
                        line=params.get("line"),
                        limit=params.get("limit"),
                    )
                }
            else:
                portal.acp_write_text(params.get("path"), params.get("content"))
                result = {}
            self._send_json({"jsonrpc": "2.0", "id": request_id, "result": result})
        except Exception as error:
            self._send_acp_error(request_id, -32602, str(error))

    def _send_acp_error(self, request_id: int | str, code: int, message: str) -> None:
        try:
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": int(code),
                        "message": clean_room_text(message, limit=1000),
                    },
                }
            )
        except RuntimeError as error:
            self._last_error = str(error)

    def _consume_notifications(
        self,
        session_id: str,
        content_parts: list[str],
        *,
        on_delta: Callable[[str], None] | None,
        on_activity: Callable[[dict[str, object]], None] | None,
    ) -> None:
        while True:
            try:
                message = self._notifications.get_nowait()
            except queue.Empty:
                return
            if message.get("_eof"):
                raise RuntimeError("Grok ACP runtime exited before turn completion.")
            method = str(message.get("method") or "")
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            if method == "_x.ai/sessions/changed":
                for session in list(params.get("upserted") or []):
                    if isinstance(session, dict) and session.get("sessionId") == session_id:
                        yolo = session.get("yolo")
                        if isinstance(yolo, bool):
                            self._yolo_mode = yolo
                            if yolo:
                                raise RuntimeError("Grok ACP safety isolation failed: always-approve mode is active.")
                continue
            if method != "session/update" or params.get("sessionId") != session_id:
                continue
            update = params.get("update") if isinstance(params.get("update"), dict) else {}
            update_type = str(update.get("sessionUpdate") or "")
            if update_type == "agent_thought_chunk":
                content = update.get("content") if isinstance(update.get("content"), dict) else {}
                self._emit_thought_activity(
                    content.get("text"),
                    on_activity=on_activity,
                )
                continue
            if update_type in {"tool_call", "tool_call_update"}:
                self._emit_thought_activity("", on_activity=on_activity, force=True)
                if on_activity is not None:
                    raw_status = str(update.get("status") or "running").casefold()
                    status = (
                        "completed"
                        if raw_status in {"cancelled", "completed", "error", "failed", "success", "done"}
                        else "running"
                    )
                    title, detail = _grok_tool_activity(update)
                    tool_call_id = clean_room_text(
                        update.get("toolCallId") or update.get("tool_call_id"),
                        limit=128,
                    )
                    if self._should_emit_tool_activity(
                        tool_call_id,
                        status=status,
                        detail=detail,
                    ):
                        on_activity(
                            {
                                "category": _tool_category(title),
                                "status": status,
                                "content": detail,
                            }
                        )
                continue
            if update_type != "agent_message_chunk":
                continue
            self._emit_thought_activity("", on_activity=on_activity, force=True)
            content = update.get("content") if isinstance(update.get("content"), dict) else {}
            delta = str(content.get("text") or "")
            if not delta:
                continue
            content_parts.append(delta)
            if on_delta is not None:
                on_delta(delta)

    def _emit_thought_activity(
        self,
        value: object,
        *,
        on_activity: Callable[[dict[str, object]], None] | None,
        force: bool = False,
    ) -> None:
        if on_activity is None:
            return
        raw = str(value or "")
        with self._lock:
            if raw:
                self._active_thought_text = (
                    self._active_thought_text + raw
                )[:2000]
            thought = clean_room_text(self._active_thought_text, limit=2000)
            previous = self._last_emitted_thought_text
            should_emit = bool(
                thought
                and thought != previous
                and (
                    force
                    or not previous
                    or len(thought) - len(previous) >= 40
                    or any(marker in raw for marker in (".", "!", "?", "\n"))
                )
            )
            if should_emit:
                self._last_emitted_thought_text = thought
        if should_emit:
            on_activity(
                {
                    "category": "reasoning",
                    "status": "running",
                    "content": thought,
                }
            )

    def _should_emit_tool_activity(
        self,
        tool_call_id: str,
        *,
        status: str,
        detail: str,
    ) -> bool:
        if not tool_call_id:
            return True
        with self._lock:
            previous = self._tool_activity_state.get(tool_call_id)
            current = (status, detail)
            if previous == current:
                return False
            self._tool_activity_state[tool_call_id] = current
            if status == "completed" and previous is not None:
                return False
        return True

    def _raise_if_exited(self) -> None:
        with self._lock:
            process = self.process
        if process is None:
            raise RuntimeError("Grok ACP runtime stopped while reading.")
        returncode = process.poll()
        if returncode is not None:
            raise RuntimeError(f"Grok ACP runtime exited with return code {returncode}.")

    def _queue_notification(
        self,
        notifications: queue.Queue[dict[str, object]],
        message: dict[str, object],
    ) -> None:
        try:
            notifications.put_nowait(message)
            return
        except queue.Full:
            pass
        try:
            notifications.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            if self._notifications is notifications:
                self._notification_drop_count += 1
        try:
            notifications.put_nowait(message)
        except queue.Full:
            with self._lock:
                if self._notifications is notifications:
                    self._notification_drop_count += 1

    def _raise_if_notifications_dropped(self, turn_start_count: int) -> None:
        with self._lock:
            dropped = self._notification_drop_count - turn_start_count
        if dropped > 0:
            raise RuntimeError(
                f"Grok ACP output backpressure dropped {dropped} structured notification(s); turn discarded."
            )

    def _running_locked(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _provider_error_detail(self, fallback: str) -> str:
        deadline = time.monotonic() + (0.25 if "internal error" in fallback.casefold() else 0.0)
        while True:
            with self._lock:
                stderr = "\n".join(self._stderr_tail).casefold()
            if "usage balance exhausted" in stderr or ("402 payment required" in stderr and "grok" in stderr):
                return "Grok provider usage balance is exhausted (402 Payment Required)."
            if "invalid authentication" in stderr or "not logged in" in stderr or "authentication required" in stderr:
                return "Grok provider authentication is required."
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        return clean_room_text(fallback, limit=1000) or "Grok ACP request failed."


def _put_nowait(target: queue.Queue[dict[str, object]], value: dict[str, object]) -> None:
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


def _tool_category(title: str) -> str:
    value = str(title or "").casefold()
    if any(word in value for word in ("read", "file", "open")):
        return "file_read"
    if any(word in value for word in ("search", "find", "grep")):
        return "search"
    if any(word in value for word in ("web", "http", "fetch", "browser")):
        return "web"
    if any(word in value for word in ("shell", "command", "exec", "terminal")):
        return "command"
    return "tool"


def _grok_tool_activity(update: dict[str, object]) -> tuple[str, str]:
    metadata = update.get("_meta") if isinstance(update.get("_meta"), dict) else {}
    tool_metadata = (
        metadata.get("x.ai/tool")
        if isinstance(metadata.get("x.ai/tool"), dict)
        else {}
    )
    raw_input = update.get("rawInput") if isinstance(update.get("rawInput"), dict) else {}
    name = clean_room_text(
        tool_metadata.get("name")
        or update.get("name")
        or update.get("title"),
        limit=120,
    )
    label = clean_room_text(tool_metadata.get("label") or name, limit=120)
    title = clean_room_text(update.get("title"), limit=600)
    category_source = " ".join(part for part in (name, label, title) if part)

    detail_value = ""
    for key in (
        "command",
        "target_file",
        "file_path",
        "path",
        "pattern",
        "query",
        "url",
        "target_directory",
        "description",
    ):
        candidate = raw_input.get(key)
        if isinstance(candidate, str) and candidate.strip():
            detail_value = candidate
            break
    if detail_value:
        detail = f"{label or name or 'Tool'}: {detail_value}"
    else:
        detail = title or label or name
    return category_source, clean_room_text(detail, limit=600)


def _resolve_executable(executable: str) -> str:
    if not executable:
        return ""
    path = Path(executable).expanduser()
    if path.is_absolute() or "/" in executable:
        return str(path.resolve()) if path.is_file() else ""
    return shutil.which(executable) or ""


def _terminate_process(process: subprocess.Popen[str], *, timeout_seconds: float) -> None:
    if process.poll() is not None:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=timeout_seconds)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            try:
                if hasattr(os, "killpg"):
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass


def _close_stream(stream: TextIO | None) -> None:
    if stream is None:
        return
    try:
        stream.close()
    except OSError:
        pass


def _now() -> str:
    return datetime.now(UTC).isoformat()
