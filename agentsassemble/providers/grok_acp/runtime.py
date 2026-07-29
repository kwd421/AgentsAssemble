from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from agentsassemble.providers.process_environment import sanitized_provider_environment
from agentsassemble.providers.grok_acp.process import (
    close_stream as _close_stream,
    resolve_executable as _resolve_executable,
    terminate_process as _terminate_process,
)
from agentsassemble.providers.grok_acp.room_access import (
    merge_permission_context,
    permission_context_update,
    permission_is_room_mcp_tool,
    permission_tool_call_id,
)
from agentsassemble.providers.grok_acp.session import GrokAcpSessionStore
from agentsassemble.providers.grok_acp.turns import GrokAcpTurnProjectionMixin
from agentsassemble.providers.grok_acp.transport import GrokAcpTransportMixin
from agentsassemble.providers.room_portal_mcp import room_portal_mcp_settings
from agentsassemble.providers.runtime_contracts import AdapterContractError
from agentsassemble.room.text import clean_room_text

if TYPE_CHECKING:
    from agentsassemble.providers.room_portal import RoomPortal


def _permission_option_id(params: dict[str, object], kind: str) -> str:
    for option in list(params.get("options") or []):
        if not isinstance(option, dict) or str(option.get("kind") or "") != kind:
            continue
        option_id = clean_room_text(option.get("optionId"), limit=128)
        if option_id:
            return option_id
    return ""


class GrokAcpRuntime(GrokAcpTransportMixin, GrokAcpTurnProjectionMixin):
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
        self._session_store = GrokAcpSessionStore(self.state_dir, self.cwd)
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
                            "readTextFile": False,
                            "writeTextFile": False,
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
                    {
                        "cwd": str(self.cwd),
                        "mcpServers": self._room_mcp_servers(),
                    },
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
                "room_mcp_configured": bool(self._room_mcp_servers()),
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
                    "mcpServers": self._room_mcp_servers(),
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
        return self._session_store.path

    def _read_provider_session(self) -> str:
        return self._session_store.read()

    def _persist_provider_session(self, session_id: str) -> None:
        self._session_store.persist(session_id)

    def _respond_to_permission_request(self, message: dict[str, object]) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
            return
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        tool_call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
        allow_room_mcp = self._permission_is_room_mcp_tool(params, tool_call)
        allow_option_id = ""
        if allow_room_mcp:
            allow_option_id = _permission_option_id(params, "allow_once")
        allow_request = bool(allow_option_id)
        option_kind = "allow_once" if allow_request else "reject_once"
        option_id = (
            allow_option_id
            if allow_request
            else _permission_option_id(params, option_kind)
        )
        with self._lock:
            self._permission_request_count += 1
            if not allow_request:
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

    def _permission_is_room_mcp_tool(
        self,
        params: dict[str, object],
        tool_call: dict[str, object],
    ) -> bool:
        with self._lock:
            active_room_observation = self._active_room_observation
            session_id = self._session_id
        if self.room_portal is None:
            return False
        tool_call_id = permission_tool_call_id(params, tool_call)
        with self._lock:
            cached = dict(
                self._tool_permission_context.get((session_id, tool_call_id)) or {}
            )
        return permission_is_room_mcp_tool(
            params,
            tool_call,
            session_id=session_id,
            active_room_observation=active_room_observation,
            cached=cached,
        )

    def _room_mcp_servers(self) -> list[dict[str, object]]:
        portal = self.room_portal
        if portal is None:
            return []
        settings = room_portal_mcp_settings(portal.root)
        return [
            {
                "name": "agentsassemble_room",
                "command": str(settings["command"]),
                "args": [str(value) for value in settings.get("args", [])],
                "env": [
                    {
                        "name": "PYTHONPATH",
                        "value": str(settings["cwd"]),
                    }
                ],
            }
        ]

    def _remember_tool_permission_context(self, message: dict[str, object]) -> None:
        with self._lock:
            active_session_id = self._session_id
            active_room_observation = self._active_room_observation
        update = permission_context_update(
            message,
            active_session_id=active_session_id,
            active_room_observation=active_room_observation,
        )
        if update is None:
            return
        context_key, incoming = update
        with self._lock:
            previous = dict(self._tool_permission_context.get(context_key) or {})
            self._tool_permission_context[context_key] = merge_permission_context(
                previous,
                incoming,
            )
            while len(self._tool_permission_context) > 256:
                self._tool_permission_context.pop(next(iter(self._tool_permission_context)))
    def _reject_unsupported_client_request(self, message: dict[str, object]) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
            return
        method = str(message.get("method") or "")
        try:
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Unsupported ACP client method: {method}",
                    },
                }
            )
        except RuntimeError as error:
            self._last_error = str(error)

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


def _now() -> str:
    return datetime.now(UTC).isoformat()
