"""Codex app-server process, JSON-RPC, and runtime profile ownership."""

from __future__ import annotations

from collections import deque
import json
from pathlib import Path
import queue
import shlex
import subprocess
import threading
import time
from typing import Callable, Iterable

from agentsassemble.diagnostics.sensitive_text import redact_persisted_diagnostic_value
from agentsassemble.providers.turn_input import agent_turn_prompt
from agentsassemble.providers.process_environment import sanitized_provider_environment
from agentsassemble.providers.codex_provider_requests import (
    CODEX_PROVIDER_REQUEST_METHODS,
    handle_codex_provider_request,
)
from agentsassemble.providers.codex_model_provider import (
    codex_model_provider_command_args,
)
from agentsassemble.providers.provider_requests import ProviderRequestHandler
from agentsassemble.providers.turn_progress import (
    DEFAULT_PROVIDER_INACTIVITY_TIMEOUT_SECONDS,
    ProviderTurnProgress,
    run_during_provider_wait,
)
from agentsassemble.room.projection import (
    safe_activity_detail,
    safe_activity_display_detail,
)
from agentsassemble.room.text import clean_room_text

AgentTurnChunk = dict[str, object]
ProcessFactory = Callable[[], object]
DynamicToolHandler = Callable[[str, object], dict[str, object]]
DEFAULT_AGENT_TURN_TIMEOUT_SECONDS = DEFAULT_PROVIDER_INACTIVITY_TIMEOUT_SECONDS
CODEX_APP_SERVER_STDERR_TAIL_LINES = 50
CODEX_APP_SERVER_STDERR_TAIL_CHARS = 16000
CODEX_APP_SERVER_METHOD_TAIL_LENGTH = 50
CODEX_APP_SERVER_IDLE_COMPLETION_GRACE_SECONDS = 1.0
CODEX_APP_SERVER_INFERRED_TURN_COMPLETED_METHOD = "agentsassemble/turn_inferred_completed"
DEFAULT_CODEX_APP_SERVER_RUNTIME_SHARING_POLICY = "isolated_session"
CODEX_APP_SERVER_RUNTIME_SHARING_POLICIES = {"isolated_session", "shared_profile", "shared_profile_serial"}


def clean_agent_session_provider_kind(value: object) -> str:
    provider = clean_room_text(value, limit=64)
    aliases = {
        "codex": "codex_live_session",
        "codex-cli": "codex_live_session",
        "codex_cli": "codex_live_session",
    }
    return aliases.get(provider, provider)


class CodexAppServerRuntime:
    """Codex app-server adapter for low-latency Agent Sessions.

    This is provider-facing plumbing only; UI remains "Agent Session".
    """

    def __init__(
        self,
        *,
        process_factory: ProcessFactory | None = None,
        command: list[str] | None = None,
        runtime_profile_key: str = "",
        profile_settings: dict[str, object] | None = None,
        environment: dict[str, str] | None = None,
        dynamic_tools: list[dict[str, object]] | None = None,
        dynamic_tool_handler: DynamicToolHandler | None = None,
        provider_request_handler: ProviderRequestHandler | None = None,
    ) -> None:
        self.process_factory = process_factory
        self.command = command or ["codex", "app-server", "--stdio"]
        self.runtime_profile_key = runtime_profile_key
        self.profile_settings = profile_settings or {}
        self.environment = dict(environment or {})
        self.dynamic_tools = [dict(item) for item in list(dynamic_tools or [])]
        self.dynamic_tool_handler = dynamic_tool_handler
        self.provider_request_handler = provider_request_handler
        self.process: object | None = None
        self._next_id = 1
        self._initialized = False
        self._pending_messages: list[dict[str, object]] = []
        self._thread_handles: dict[str, dict[str, object]] = {}
        self._stderr_lock = threading.Lock()
        self._diagnostics_lock = threading.RLock()
        self._turn_lock = threading.RLock()
        self._stdout_queue: queue.Queue[object] = queue.Queue()
        self._stdout_eof = object()
        self._stdout_thread: threading.Thread | None = None
        self._stderr_tail: deque[str] = deque()
        self._app_server_method_tail: deque[str] = deque(maxlen=CODEX_APP_SERVER_METHOD_TAIL_LENGTH)
        self._stderr_thread: threading.Thread | None = None
        self._stderr_line_count = 0
        self._stderr_byte_count = 0
        self._stderr_warning_count = 0
        self._stderr_tail_truncated = False
        self._stderr_last_line_at = ""
        self._unmatched_notification_count = 0
        self._dynamic_tool_call_count = 0
        self._dynamic_tool_error_count = 0
        self.diagnostics: dict[str, object] = {
            "runtime_mode": "app_server",
            "transport": "stdio_jsonl",
            "runtime_reused": False,
            "runtime_profile_key": runtime_profile_key,
        }
        self._update_diagnostics(self.profile_settings)

    def start(self, config: dict[str, object]) -> dict[str, object]:
        started = time.monotonic()
        if self.process is None:
            self._reset_stderr_drain_state()
            self.process = self._spawn_process()
            self._update_diagnostics({"app_server_pid": getattr(self.process, "pid", "")})
            self._start_stdout_drain()
            self._start_stderr_drain()
        else:
            self._update_diagnostics({"runtime_reused": True, "app_server_reused": True})
        if not self._initialized:
            initialize_params: dict[str, object] = {
                "clientInfo": {"name": "AgentsAssemble", "version": "0"}
            }
            if self.dynamic_tools:
                initialize_params["capabilities"] = {"experimentalApi": True}
            self._send_request("initialize", initialize_params)
            self._send_notification("initialized", {})
            self._initialized = True
        self._update_diagnostics({"app_server_initialize_ms": _elapsed_ms(started)})
        return {"runtime_mode": "app_server", "transport": "stdio_jsonl", **config}

    def attach(self, ids: dict[str, object]) -> dict[str, object]:
        self.start({})
        provider_session_id = clean_provider_session_id(ids.get("provider_session_id"))
        provider_thread_id = clean_provider_session_id(ids.get("provider_thread_id"))
        session_id = clean_room_text(ids.get("session_id"), limit=128)
        cached = self._cached_thread(provider_session_id=provider_session_id, provider_thread_id=provider_thread_id, session_id=session_id)
        if cached:
            self._update_diagnostics({"thread_reused": True, "thread_resume_skipped": True})
            return cached
        started = time.monotonic()
        if provider_thread_id or provider_session_id:
            response = self._send_request("thread/resume", {"threadId": provider_thread_id or provider_session_id})
            self._update_diagnostics({"thread_resume_ms": _elapsed_ms(started)})
            thread_id = clean_provider_session_id(_nested_get(response, "result.thread.id") or provider_thread_id or provider_session_id)
            self._update_diagnostics({"thread_reused": False, "thread_resume_skipped": False})
        else:
            thread_start_settings = _codex_app_server_thread_start_settings(
                self.profile_settings
            )
            if self.dynamic_tools:
                thread_start_settings["dynamicTools"] = self.dynamic_tools
            response = self._send_request("thread/start", thread_start_settings)
            self._update_diagnostics({"thread_start_ms": _elapsed_ms(started)})
            thread_id = clean_provider_session_id(
                _nested_get(response, "result.thread.id")
                or _nested_get(response, "result.threadId")
                or _nested_get(response, "params.thread.id")
            )
            self._update_diagnostics({"thread_reused": False, "thread_resume_skipped": False})
        observed_model_id = _codex_app_server_observed_model(response)
        if observed_model_id:
            self._update_diagnostics({"observed_model_id": observed_model_id})
        handle = {
            "runtime_mode": "app_server",
            "transport": "stdio_jsonl",
            "provider_thread_id": thread_id,
            "provider_session_id": thread_id,
        }
        if session_id:
            handle["session_id"] = session_id
        self._cache_thread(handle)
        return handle

    def send_turn(self, handle: dict[str, object], packet: dict[str, object]) -> Iterable[AgentTurnChunk]:
        self._reset_turn_diagnostics()
        try:
            attached = self.attach(handle)
        except Exception as error:
            had_resume_id = bool(clean_provider_session_id(handle.get("provider_thread_id") or handle.get("provider_session_id")))
            self._handle_process_failure(error)
            yield {
                "type": "error",
                "diagnostics": self._error_diagnostics(
                    error,
                    status="resume_failed" if had_resume_id else "start_failed",
                    recovery_required=had_resume_id,
                ),
            }
            return
        thread_id = clean_provider_session_id(attached.get("provider_thread_id") or handle.get("provider_session_id"))
        if thread_id:
            yield {"type": "provider_session", "provider_session_id": thread_id, "provider_thread_id": thread_id}
        yield from self._send_turn_attached(thread_id, packet)

    def _send_turn_attached(self, thread_id: str, packet: dict[str, object]) -> Iterable[AgentTurnChunk]:
        with self._turn_lock:
            yield from self._send_turn_attached_locked(thread_id, packet)

    def _send_turn_attached_locked(self, thread_id: str, packet: dict[str, object]) -> Iterable[AgentTurnChunk]:
        started = time.monotonic()
        packet_input = packet.get("input")
        turn_input = (
            [dict(item) for item in packet_input if isinstance(item, dict)]
            if isinstance(packet_input, list)
            else []
        )
        if not turn_input:
            turn_input = [
                {
                    "type": "text",
                    "text": str(
                        packet.get("provider_input") or agent_turn_prompt(packet)
                    ),
                }
            ]
        turn_params = {
            "threadId": thread_id,
            "input": turn_input,
            "metadata": {"source": "agentsassemble_agent_session"},
            **_codex_app_server_turn_start_settings(self.profile_settings),
        }
        timeout_seconds = _agent_turn_timeout_seconds(packet.get("timeout_seconds"))
        progress = ProviderTurnProgress(timeout_seconds)
        try:
            turn_response = self._send_request("turn/start", turn_params, timeout_deadline=progress.deadline)
        except Exception as error:
            self._handle_process_failure(error)
            yield {
                "type": "error",
                "diagnostics": self._error_diagnostics(error, status="turn_start_failed", recovery_required=bool(thread_id)),
            }
            return
        progress.record()
        provider_turn_id = clean_provider_session_id(
            _nested_get(turn_response, "result.turn.id")
            or _nested_get(turn_response, "result.turnId")
            or _nested_get(turn_response, "params.turn.id")
            or _nested_get(turn_response, "params.turnId")
        )
        turn_start_request_ms = _elapsed_ms(started)
        self._update_diagnostics(
            {
                "provider_thread_id": thread_id,
                "provider_turn_id": provider_turn_id,
                "turn_start_request_ms": turn_start_request_ms,
                "time_to_turn_start_ack_ms": turn_start_request_ms,
            }
        )
        first_notification = False
        first_agent_item = False
        first_text_delta = False
        turn_event_count = 0
        try:
            messages = self._read_messages_until_turn_done(
                thread_id=thread_id,
                turn_id=provider_turn_id,
                progress=progress,
            )
            for message in messages:
                method = clean_room_text(message.get("method"), limit=128)
                turn_event_count += 1
                self._record_app_server_message(message, turn_event_count=turn_event_count)
                params = message.get("params") if isinstance(message.get("params"), dict) else {}
                if not first_notification:
                    self._update_diagnostics({"time_to_first_notification_ms": _elapsed_ms(started)})
                    first_notification = True
                if method in {"turn/started"}:
                    yield {"type": "diagnostics", **self._diagnostics_snapshot()}
                    continue
                if method in {"item/started", "item/completed"}:
                    activity = _app_server_activity(
                        params,
                        completed=method == "item/completed",
                    )
                    if activity:
                        yield {"type": "thinking_delta", **activity}
                        continue
                if method in {"agent_message/delta", "agent-message/delta", "item/agent_message/delta", "item/agentMessage/delta"}:
                    if not first_agent_item:
                        first_item_ms = _elapsed_ms(started)
                        self._update_diagnostics(
                            {
                                "time_to_first_agent_item_ms": first_item_ms,
                                "time_to_first_item_event_ms": first_item_ms,
                            }
                        )
                        first_agent_item = True
                    if not first_text_delta:
                        first_delta_ms = _elapsed_ms(started)
                        self._update_diagnostics(
                            {
                                "time_to_first_agent_text_delta_ms": first_delta_ms,
                                "time_to_first_agent_delta_ms": first_delta_ms,
                            }
                        )
                        first_text_delta = True
                    yield {"type": "message_delta", "content": clean_room_text(params.get("delta") or params.get("text"), limit=8000)}
                    continue
                if method in {"agent_message/completed", "agent-message/completed", "item/agent_message/completed", "item/completed"}:
                    item = params.get("item") if isinstance(params.get("item"), dict) else {}
                    if method == "item/completed" and clean_room_text(item.get("type"), limit=64) != "agentMessage":
                        continue
                    self._update_diagnostics({"time_to_message_final_ms": _elapsed_ms(started)})
                    yield {
                        "type": "message_final",
                        "content": clean_room_text(params.get("text") or params.get("content") or item.get("text"), limit=8000),
                    }
                    continue
                if method in {"turn/completed", CODEX_APP_SERVER_INFERRED_TURN_COMPLETED_METHOD}:
                    updates: dict[str, object] = {"turn_completed_ms": _elapsed_ms(started)}
                    if method == CODEX_APP_SERVER_INFERRED_TURN_COMPLETED_METHOD:
                        updates.update(
                            {
                                "app_server_completion_signal": "agent_message_final_thread_idle",
                                "app_server_completion_inferred": True,
                            }
                        )
                    else:
                        updates.update({"app_server_completion_signal": "turn_completed", "app_server_completion_inferred": False})
                    self._update_diagnostics(updates)
                    yield {"type": "diagnostics", **self._diagnostics_snapshot()}
                    return
                if method in {"command_execution/request_approval", "file_change/request_approval", "permissions/request_approval"}:
                    yield {"type": "approval_requested", "diagnostics": [{"setting": "approval", "status": "requested", "message": method}]}
                    continue
                if method in {"context/compaction_started"}:
                    self._update_diagnostics({"compaction_started_ms": _elapsed_ms(started)})
                    yield {"type": "context_compaction_started"}
                    continue
                if method in {"context/compaction_finished"}:
                    self._update_diagnostics({"compaction_completed_ms": _elapsed_ms(started)})
                    yield {"type": "context_compaction_finished"}
                    continue
                if method in {"turn/error", "error"} or _context_error_detected(message):
                    yield {
                        "type": "error",
                        "diagnostics": [
                            {
                                "setting": "app_server",
                                "status": "failed",
                                "message": clean_room_text(params.get("message") or str(message), limit=1000),
                            },
                            *self._diagnostic_snapshot_items(),
                        ],
                    }
                    return
        except Exception as error:
            self._handle_process_failure(error)
            yield {
                "type": "error",
                "diagnostics": self._error_diagnostics(error, status="stopped", recovery_required=bool(thread_id)),
            }
            return

    def compact(self, handle: dict[str, object], policy: dict[str, object]) -> Iterable[AgentTurnChunk]:
        self._send_notification("thread/compact", {"threadId": handle.get("provider_thread_id"), "policy": policy})
        return []

    def detach(self, handle: dict[str, object]) -> None:
        self.release_thread(handle)
        process = self.process
        if process is not None:
            self._terminate_process(process, timeout_seconds=5)
            self._close_process_streams(process)
            self._join_stdout_drain()
            self._join_stderr_drain()
            self.process = None
        self._initialized = False
        self._pending_messages.clear()
        self._thread_handles.clear()

    def diagnose(self, handle: dict[str, object]) -> dict[str, object]:
        return self._diagnostics_snapshot()

    def read_account_rate_limits(self) -> dict[str, object]:
        """Read account limits without creating a provider thread or model turn."""
        self.start({})
        with self._turn_lock:
            response = self._send_request("account/rateLimits/read", {})
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Codex app-server returned an invalid rate-limit response.")
        return result

    def release_thread(self, handle: dict[str, object]) -> None:
        for key in (
            clean_provider_session_id(handle.get("provider_session_id")),
            clean_provider_session_id(handle.get("provider_thread_id")),
            clean_room_text(handle.get("session_id"), limit=128),
        ):
            if key:
                self._thread_handles.pop(key, None)

    def _spawn_process(self) -> object:
        if self.process_factory is not None:
            return self.process_factory()
        return subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            bufsize=1,
            env=sanitized_provider_environment(self.environment),
        )

    def _reset_stderr_drain_state(self) -> None:
        with self._stderr_lock:
            self._stderr_tail.clear()
            self._stderr_line_count = 0
            self._stderr_byte_count = 0
            self._stderr_warning_count = 0
            self._stderr_tail_truncated = False
            self._stderr_last_line_at = ""
        self._publish_stderr_diagnostics(drained=False)

    def _start_stderr_drain(self) -> None:
        if self.process is None:
            return
        stderr = getattr(self.process, "stderr", None)
        if stderr is None:
            self._publish_stderr_diagnostics(drained=False)
            return
        self._publish_stderr_diagnostics(drained=True)
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(stderr,),
            name="codex-app-server-stderr-drain",
            daemon=True,
        )
        self._stderr_thread.start()

    def _start_stdout_drain(self) -> None:
        if self.process is None:
            return
        stdout = getattr(self.process, "stdout", None)
        if stdout is None:
            return
        self._stdout_queue = queue.Queue()
        self._stdout_thread = threading.Thread(
            target=self._drain_stdout,
            args=(stdout,),
            name="CodexAppServerStdout",
            daemon=True,
        )
        self._stdout_thread.start()

    def _drain_stdout(self, stdout: object) -> None:
        try:
            while True:
                line = stdout.readline()
                if line in ("", b""):
                    break
                self._stdout_queue.put(line)
        except Exception as error:
            self._stdout_queue.put(error)
        finally:
            self._stdout_queue.put(self._stdout_eof)

    def _drain_stderr(self, stderr: object) -> None:
        while True:
            try:
                line = stderr.readline()
            except Exception as error:  # pragma: no cover - defensive for real subprocess streams
                self._record_stderr_line(f"stderr drain failed: {error}\n")
                return
            if line in {"", b""}:
                return
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            self._record_stderr_line(str(line))

    def _record_stderr_line(self, line: str) -> None:
        safe_line = clean_room_text(line.rstrip("\r\n"), limit=1000)
        byte_count = len(line.encode("utf-8", errors="replace"))
        with self._stderr_lock:
            self._stderr_line_count += 1
            self._stderr_byte_count += byte_count
            lowered = safe_line.lower()
            if "warn" in lowered or "warning" in lowered:
                self._stderr_warning_count += 1
            if len(self._stderr_tail) >= CODEX_APP_SERVER_STDERR_TAIL_LINES:
                self._stderr_tail_truncated = True
            self._stderr_tail.append(safe_line)
            while len(self._stderr_tail) > CODEX_APP_SERVER_STDERR_TAIL_LINES:
                self._stderr_tail.popleft()
                self._stderr_tail_truncated = True
            while len("\n".join(self._stderr_tail)) > CODEX_APP_SERVER_STDERR_TAIL_CHARS and self._stderr_tail:
                self._stderr_tail.popleft()
                self._stderr_tail_truncated = True
            self._stderr_last_line_at = _now_iso()
        self._publish_stderr_diagnostics(drained=True)

    def _stderr_diagnostics_snapshot(self, *, drained: bool | None = None) -> dict[str, object]:
        with self._stderr_lock:
            snapshot = {
                "stderr_drained": drained if drained is not None else self._diagnostics_snapshot().get("stderr_drained", False),
                "stderr_line_count": self._stderr_line_count,
                "stderr_byte_count": self._stderr_byte_count,
                "stderr_tail": "\n".join(self._stderr_tail),
                "stderr_tail_truncated": self._stderr_tail_truncated,
                "stderr_warning_count": self._stderr_warning_count,
            }
            if self._stderr_last_line_at:
                snapshot["stderr_last_line_at"] = self._stderr_last_line_at
            return snapshot

    def _publish_stderr_diagnostics(self, *, drained: bool | None = None) -> None:
        self._update_diagnostics(self._stderr_diagnostics_snapshot(drained=drained))

    def _update_diagnostics(self, updates: dict[str, object]) -> None:
        if not updates:
            return
        with self._diagnostics_lock:
            self.diagnostics.update(updates)

    def _diagnostics_snapshot(self) -> dict[str, object]:
        with self._diagnostics_lock:
            return dict(self.diagnostics)

    def _diagnostic_snapshot_items(self) -> list[dict[str, str]]:
        return _diagnostic_items(self._diagnostics_snapshot())

    def _error_diagnostics(
        self,
        error: Exception,
        *,
        status: str,
        recovery_required: bool,
    ) -> list[dict[str, str]]:
        diagnostics = self._diagnostic_snapshot_items()
        diagnostics.append(
            {
                "setting": "app_server",
                "status": status,
                "message": clean_room_text(str(error), limit=1000) or error.__class__.__name__,
            }
        )
        if recovery_required:
            diagnostics.append(
                {
                    "setting": "recovery_required",
                    "status": "true",
                    "message": "Provider thread could not complete; restart the runtime and seed the next turn from RoomMemory.",
                }
            )
        return diagnostics

    def _reset_turn_diagnostics(self) -> None:
        self._update_diagnostics(
            {
                "app_server_error": "",
                "app_server_completion_signal": "",
                "app_server_completion_inferred": "",
                "compaction_completed_ms": "",
                "compaction_started_ms": "",
                "thread_reused": "",
                "app_server_last_event_at": "",
                "app_server_last_method": "",
                "app_server_last_thread_status": "",
                "app_server_last_turn_status": "",
                "app_server_method_tail": "",
                "app_server_turn_event_count": "",
                "mcp_elicitation_meta_keys": "",
                "mcp_elicitation_mode": "",
                "mcp_elicitation_server_name": "",
                "provider_thread_id": "",
                "provider_turn_id": "",
                "pending_notification_count": "",
                "unmatched_notification_count": "",
                "time_to_first_agent_delta_ms": "",
                "time_to_first_agent_item_ms": "",
                "time_to_first_agent_text_delta_ms": "",
                "time_to_first_item_event_ms": "",
                "time_to_first_notification_ms": "",
                "time_to_message_final_ms": "",
                "time_to_turn_start_ack_ms": "",
                "turn_completed_ms": "",
                "turn_start_request_ms": "",
            }
        )
        self._app_server_method_tail.clear()
        self._unmatched_notification_count = 0

    def _handle_process_failure(self, error: Exception) -> None:
        self._update_diagnostics(
            {
                "app_server_error": clean_room_text(str(error), limit=1000) or error.__class__.__name__,
                "app_server_alive": False,
            }
        )
        process = self.process
        if process is not None:
            self._terminate_process(process, timeout_seconds=1)
            self._close_process_streams(process)
            self._join_stdout_drain()
            self._join_stderr_drain()
        self.process = None
        self._initialized = False
        self._pending_messages.clear()
        self._thread_handles.clear()

    def _join_stderr_drain(self) -> None:
        thread = self._stderr_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1)
        self._stderr_thread = None
        self._publish_stderr_diagnostics()

    def _join_stdout_drain(self) -> None:
        thread = self._stdout_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1)
        self._stdout_thread = None

    def _close_process_streams(self, process: object) -> None:
        for stream_name in ("stdin", "stdout", "stderr"):
            stream = getattr(process, stream_name, None)
            if stream is not None and hasattr(stream, "close"):
                try:
                    stream.close()
                except Exception:
                    pass

    @staticmethod
    def _terminate_process(process: object, *, timeout_seconds: float) -> None:
        if hasattr(process, "terminate"):
            try:
                process.terminate()
            except ProcessLookupError:
                return
        if not hasattr(process, "wait"):
            return
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            if hasattr(process, "kill"):
                process.kill()
                process.wait(timeout=timeout_seconds)
        except ProcessLookupError:
            pass

    def _send_request(
        self,
        method: str,
        params: dict[str, object],
        *,
        timeout_deadline: float | None = None,
    ) -> dict[str, object]:
        request_id = self._next_id
        self._next_id += 1
        self._write_json({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return self._read_response(request_id, timeout_deadline=timeout_deadline)

    def _send_notification(self, method: str, params: dict[str, object]) -> None:
        self._write_json({"jsonrpc": "2.0", "method": method, "params": params})

    def _write_json(self, payload: dict[str, object]) -> None:
        assert self.process is not None
        stdin = getattr(self.process, "stdin", None)
        if stdin is None:
            raise RuntimeError("Codex app-server stdin is unavailable.")
        stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        if hasattr(stdin, "flush"):
            stdin.flush()

    def _read_response(self, request_id: int, *, timeout_deadline: float | None = None) -> dict[str, object]:
        while True:
            message = self._read_protocol_message(timeout_deadline=timeout_deadline)
            if message.get("id") == request_id:
                return message
            self._pending_messages.append(message)
            self._publish_pending_notification_count()

    def _read_messages_until_turn_done(
        self,
        *,
        thread_id: str = "",
        turn_id: str = "",
        progress: ProviderTurnProgress,
    ) -> Iterable[dict[str, object]]:
        agent_message_completed = False
        thread_idle_after_agent_message = False
        inferred_completion_deadline: float | None = None
        while True:
            try:
                message = self._pop_matching_pending_message(thread_id=thread_id, turn_id=turn_id)
                if message is None:
                    read_deadline = _earlier_deadline(progress.deadline, inferred_completion_deadline)
                    message = self._read_protocol_message(
                        timeout_deadline=read_deadline,
                        progress=progress,
                    )
                    if not self._message_matches_active_turn(message, thread_id=thread_id, turn_id=turn_id):
                        self._buffer_unmatched_notification(message)
                        continue
            except TimeoutError:
                if inferred_completion_deadline is not None:
                    self._update_diagnostics(
                        {
                            "app_server_completion_signal": "agent_message_final_thread_idle",
                            "app_server_completion_inferred": True,
                        }
                    )
                    yield {
                        "method": CODEX_APP_SERVER_INFERRED_TURN_COMPLETED_METHOD,
                        "params": {
                            "threadId": thread_id,
                            "turnId": turn_id,
                            "turn": {"id": turn_id, "status": "completed"},
                            "completionSignal": "agent_message_final_thread_idle",
                            "inferred": True,
                        },
                    }
                    return
                raise
            progress.record()
            yield message
            method = clean_room_text(message.get("method"), limit=128)
            if method in {"turn/completed", "turn/error", "error"}:
                return
            if _app_server_agent_message_completed(message):
                agent_message_completed = True
            if agent_message_completed and _app_server_thread_idle(message):
                thread_idle_after_agent_message = True
            if thread_idle_after_agent_message and inferred_completion_deadline is None:
                inferred_completion_deadline = time.monotonic() + CODEX_APP_SERVER_IDLE_COMPLETION_GRACE_SECONDS

    def _pop_matching_pending_message(self, *, thread_id: str, turn_id: str) -> dict[str, object] | None:
        for index, message in enumerate(self._pending_messages):
            if self._message_matches_active_turn(message, thread_id=thread_id, turn_id=turn_id):
                matched = self._pending_messages.pop(index)
                self._publish_pending_notification_count()
                return matched
        return None

    def _buffer_unmatched_notification(self, message: dict[str, object]) -> None:
        self._pending_messages.append(message)
        self._unmatched_notification_count += 1
        self._publish_pending_notification_count()

    def _publish_pending_notification_count(self) -> None:
        self._update_diagnostics(
            {
                "pending_notification_count": len(self._pending_messages),
                "unmatched_notification_count": self._unmatched_notification_count,
            }
        )

    def _message_matches_active_turn(self, message: dict[str, object], *, thread_id: str, turn_id: str) -> bool:
        method = clean_room_text(message.get("method"), limit=128)
        if not method:
            return True
        message_thread_id = _app_server_message_thread_id(message)
        message_turn_id = _app_server_message_turn_id(message)
        if message_thread_id and thread_id and message_thread_id != thread_id:
            return False
        if message_turn_id and turn_id and message_turn_id != turn_id:
            return False
        return True

    def _record_app_server_message(self, message: dict[str, object], *, turn_event_count: int) -> None:
        method = clean_room_text(message.get("method"), limit=128)
        if method:
            self._app_server_method_tail.append(method)
        updates: dict[str, object] = {
            "app_server_last_method": method,
            "app_server_last_event_at": _now_iso(),
            "app_server_method_tail": " -> ".join(self._app_server_method_tail),
            "app_server_turn_event_count": turn_event_count,
            "pending_notification_count": len(self._pending_messages),
            "unmatched_notification_count": self._unmatched_notification_count,
        }
        thread_status = _app_server_message_thread_status(message)
        turn_status = _app_server_message_turn_status(message)
        if thread_status:
            updates["app_server_last_thread_status"] = thread_status
        if turn_status:
            updates["app_server_last_turn_status"] = turn_status
        observed_model_id = _codex_app_server_observed_model(message)
        if observed_model_id:
            updates["observed_model_id"] = observed_model_id
        self._update_diagnostics(updates)

    def _read_protocol_message(
        self,
        *,
        timeout_deadline: float | None = None,
        progress: ProviderTurnProgress | None = None,
    ) -> dict[str, object]:
        while True:
            message = self._read_json_line(timeout_deadline=timeout_deadline)
            method = clean_room_text(message.get("method"), limit=128)
            if method == "item/tool/call" and "id" in message:
                run_during_provider_wait(progress, lambda: self._handle_dynamic_tool_request(message))
                continue
            if method in CODEX_PROVIDER_REQUEST_METHODS and "id" in message:
                run_during_provider_wait(
                    progress,
                    lambda: handle_codex_provider_request(
                        message, handler=self.provider_request_handler, write_json=self._write_json
                    ),
                )
                continue
            if method == "mcpServer/elicitation/request" and "id" in message:
                self._record_mcp_elicitation_shape(message)
                if _is_agentsassemble_room_mcp_approval(message):
                    self._write_json(
                        {
                            "jsonrpc": "2.0",
                            "id": message.get("id"),
                            "result": {"action": "accept", "content": {}},
                        }
                    )
                    continue
                params = (
                    message.get("params")
                    if isinstance(message.get("params"), dict)
                    else {}
                )
                metadata = (
                    params.get("_meta")
                    if isinstance(params.get("_meta"), dict)
                    else {}
                )
                raise RuntimeError(
                    "Codex requested an unrecognized MCP approval "
                    f"(server={clean_room_text(params.get('serverName'), limit=128) or 'missing'}, "
                    f"mode={clean_room_text(params.get('mode'), limit=64) or 'missing'}, "
                    "meta_keys="
                    f"{','.join(sorted(clean_room_text(key, limit=128) for key in metadata if clean_room_text(key, limit=128))) or 'none'})."
                )
            return message

    def _record_mcp_elicitation_shape(self, message: dict[str, object]) -> None:
        """Keep only routing metadata needed to diagnose app-server drift."""
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        metadata = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        self._update_diagnostics(
            {
                "mcp_elicitation_server_name": clean_room_text(
                    params.get("serverName"),
                    limit=128,
                ),
                "mcp_elicitation_mode": clean_room_text(
                    params.get("mode"),
                    limit=64,
                ),
                "mcp_elicitation_meta_keys": ",".join(
                    sorted(
                        clean_room_text(key, limit=128)
                        for key in metadata
                        if clean_room_text(key, limit=128)
                    )
                ),
            }
        )

    def _handle_dynamic_tool_request(self, message: dict[str, object]) -> None:
        request_id = message.get("id")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        tool = clean_room_text(params.get("tool"), limit=128)
        self._dynamic_tool_call_count += 1
        try:
            if self.dynamic_tool_handler is None:
                raise RuntimeError("No dynamic tool handler is configured.")
            result = self.dynamic_tool_handler(tool, params.get("arguments"))
            if not isinstance(result, dict):
                raise RuntimeError("Dynamic tool handler returned an invalid response.")
        except Exception:
            self._dynamic_tool_error_count += 1
            result = {
                "success": False,
                "contentItems": [
                    {
                        "type": "inputText",
                        "text": "The shared-room tool could not complete this request.",
                    }
                ],
            }
        self._update_diagnostics(
            {
                "dynamic_tool_call_count": self._dynamic_tool_call_count,
                "dynamic_tool_error_count": self._dynamic_tool_error_count,
            }
        )
        self._write_json({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _read_json_line(self, *, timeout_deadline: float | None = None) -> dict[str, object]:
        assert self.process is not None
        if self._stdout_thread is None:
            raise RuntimeError("Codex app-server stdout is unavailable.")
        try:
            if timeout_deadline is None:
                queued = self._stdout_queue.get()
            else:
                remaining = timeout_deadline - time.monotonic()
                if remaining <= 0:
                    raise queue.Empty
                queued = self._stdout_queue.get(timeout=remaining)
        except queue.Empty as error:
            raise TimeoutError(
                "Codex app-server timed out before completing the request."
            ) from error
        if queued is self._stdout_eof:
            raise RuntimeError("Codex app-server stopped before completing the request.")
        if isinstance(queued, Exception):
            raise RuntimeError(
                f"Codex app-server stdout reader failed: {queued}"
            ) from queued
        line = (
            queued.decode("utf-8", errors="replace")
            if isinstance(queued, bytes)
            else str(queued)
        )
        try:
            message = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Codex app-server emitted malformed JSON: {error}") from error
        if "error" in message and not message.get("method"):
            raise RuntimeError(clean_room_text(message.get("error"), limit=1000) or "Codex app-server request failed.")
        return message

    def _cached_thread(self, *, provider_session_id: str, provider_thread_id: str, session_id: str) -> dict[str, object]:
        for key in (provider_thread_id, provider_session_id, session_id):
            if key and key in self._thread_handles:
                return dict(self._thread_handles[key])
        return {}

    def _cache_thread(self, handle: dict[str, object]) -> None:
        for key in (
            clean_provider_session_id(handle.get("provider_session_id")),
            clean_provider_session_id(handle.get("provider_thread_id")),
            clean_room_text(handle.get("session_id"), limit=128),
        ):
            if key:
                self._thread_handles[key] = dict(handle)


class CodexAppServerRuntimeManager:
    def __init__(self, *, process_factory: ProcessFactory | None = None) -> None:
        self.process_factory = process_factory
        self._runtimes: dict[str, CodexAppServerRuntime] = {}
        self._session_refs: dict[str, set[str]] = {}
        self._session_keys: dict[str, str] = {}

    def runtime_for(self, session: dict[str, object], packet: dict[str, object] | None = None) -> CodexAppServerRuntime:
        packet = packet or {}
        key = runtime_profile_key(session, packet)
        if key not in self._runtimes:
            profile_settings = runtime_profile_settings(session, packet)
            self._runtimes[key] = CodexAppServerRuntime(
                process_factory=self.process_factory,
                command=codex_app_server_runtime_command(profile_settings),
                runtime_profile_key=key,
                profile_settings=profile_settings,
            )
            self._session_refs[key] = set()
        session_id = clean_room_text(session.get("session_id"), limit=128)
        if session_id:
            self._session_refs.setdefault(key, set()).add(session_id)
            self._session_keys[session_id] = key
        return self._runtimes[key]

    def send_turn(self, session: dict[str, object], packet: dict[str, object]) -> Iterable[AgentTurnChunk]:
        runtime = self.runtime_for(session, packet)
        yield from runtime.send_turn(session, packet)

    def detach_session(self, session: dict[str, object], *, shutdown_unused: bool = True) -> None:
        session_id = clean_room_text(session.get("session_id"), limit=128)
        key = self._session_keys.get(session_id) or runtime_profile_key(session, {})
        runtime = self._runtimes.get(key)
        if runtime is None:
            return
        runtime.release_thread(session)
        refs = self._session_refs.setdefault(key, set())
        refs.discard(session_id)
        self._session_keys.pop(session_id, None)
        if shutdown_unused and not refs:
            runtime.detach({})
            self._runtimes.pop(key, None)
            self._session_refs.pop(key, None)

    def shutdown_unused(self) -> None:
        for key in list(self._runtimes):
            if not self._session_refs.get(key):
                self._runtimes[key].detach({})
                self._runtimes.pop(key, None)
                self._session_refs.pop(key, None)
        live_keys = set(self._runtimes)
        self._session_keys = {session_id: key for session_id, key in self._session_keys.items() if key in live_keys}

    def shutdown_all(self) -> None:
        for runtime in list(self._runtimes.values()):
            runtime.detach({})
        self._runtimes.clear()
        self._session_refs.clear()
        self._session_keys.clear()


def runtime_profile_key(session: dict[str, object], packet: dict[str, object] | None = None) -> str:
    parts = runtime_profile_settings(session, packet)
    return "|".join(f"{key}={value}" for key, value in sorted(parts.items()))


def runtime_profile_settings(session: dict[str, object], packet: dict[str, object] | None = None) -> dict[str, str]:
    settings = packet.get("settings") if isinstance(packet, dict) and isinstance(packet.get("settings"), dict) else {}
    runtime_sharing_policy = clean_codex_app_server_runtime_sharing_policy(
        (packet or {}).get("runtime_sharing_policy")
        or settings.get("runtime_sharing_policy")
        or session.get("runtime_sharing_policy")
    )
    profile = {
        "provider_kind": clean_agent_session_provider_kind(session.get("provider_kind")),
        "workspace": clean_room_text((packet or {}).get("workspace") or session.get("workspace") or session.get("cwd") or "", limit=300),
        "model": clean_room_text(settings.get("model") or session.get("model"), limit=128),
        "effort": clean_room_text(settings.get("effort") or session.get("effort"), limit=64),
        "sandbox": clean_room_text(settings.get("sandbox") or session.get("sandbox"), limit=64),
        "permissions": clean_room_text(settings.get("permissions") or session.get("permissions"), limit=64),
        "codex_home": clean_room_text(session.get("codex_home") or session.get("config_profile"), limit=200),
        "runtime_sharing_policy": runtime_sharing_policy,
    }
    if runtime_sharing_policy == "isolated_session":
        profile["session_id"] = clean_room_text(session.get("session_id") or session.get("participant_id"), limit=128)
    return profile


def clean_codex_app_server_runtime_sharing_policy(value: object) -> str:
    policy = clean_room_text(value, limit=64)
    if policy in CODEX_APP_SERVER_RUNTIME_SHARING_POLICIES:
        return policy
    return DEFAULT_CODEX_APP_SERVER_RUNTIME_SHARING_POLICY


def codex_app_server_runtime_command(profile_settings: dict[str, object]) -> list[str]:
    command = ["codex", "app-server"]
    model = clean_room_text(profile_settings.get("model"), limit=128)
    effort = clean_room_text(profile_settings.get("effort"), limit=64)
    service_tier = clean_room_text(profile_settings.get("service_tier"), limit=32)
    sandbox = clean_room_text(profile_settings.get("sandbox"), limit=64)
    approval_policy = _codex_approval_policy(profile_settings.get("permissions"))
    workspace_trust = _codex_workspace_trust_config(profile_settings.get("workspace"))
    room_mcp_server = profile_settings.get("room_mcp_server")
    if model:
        command.extend(["-c", _codex_toml_string_config("model", model)])
    command.extend(codex_model_provider_command_args(profile_settings))
    if effort:
        command.extend(["-c", _codex_toml_string_config("model_reasoning_effort", effort)])
    if service_tier and service_tier != "default":
        command.extend(["-c", _codex_toml_string_config("service_tier", service_tier)])
    if sandbox in {"read-only", "workspace-write", "danger-full-access"}:
        command.extend(["-c", _codex_toml_string_config("sandbox_mode", sandbox)])
    if approval_policy:
        command.extend(["-c", _codex_toml_string_config("approval_policy", approval_policy)])
    if workspace_trust:
        command.extend(["-c", workspace_trust])
    if isinstance(room_mcp_server, dict) and room_mcp_server.get("command"):
        server_key = "mcp_servers.agentsassemble_room"
        configured_args = room_mcp_server.get("args")
        server_args = (
            [str(value) for value in configured_args]
            if isinstance(configured_args, list)
            else []
        )
        server_cwd = clean_room_text(room_mcp_server.get("cwd"), limit=500)
        command.extend(
            [
                "-c",
                _codex_toml_string_config(
                    f"{server_key}.command",
                    str(room_mcp_server["command"]),
                ),
                "-c",
                f"{server_key}.args={json.dumps(server_args, ensure_ascii=True)}",
            ]
        )
        if server_cwd:
            command.extend(
                ["-c", _codex_toml_string_config(f"{server_key}.cwd", server_cwd)]
            )
    command.append("--stdio")
    return command


def _codex_app_server_thread_start_settings(profile_settings: dict[str, object]) -> dict[str, object]:
    params: dict[str, object] = {}
    workspace = _codex_workspace_path(profile_settings.get("workspace"))
    model = clean_room_text(profile_settings.get("model"), limit=128)
    sandbox = clean_room_text(profile_settings.get("sandbox"), limit=64)
    approval_policy = _codex_approval_policy(profile_settings.get("permissions"))
    if workspace:
        params["cwd"] = workspace
    if model:
        params["model"] = model
    if approval_policy:
        params["approvalPolicy"] = approval_policy
    if sandbox in {"read-only", "workspace-write", "danger-full-access"}:
        params["sandbox"] = sandbox
    return params


def _codex_app_server_turn_start_settings(profile_settings: dict[str, object]) -> dict[str, object]:
    params: dict[str, object] = {}
    workspace = _codex_workspace_path(profile_settings.get("workspace"))
    model = clean_room_text(profile_settings.get("model"), limit=128)
    effort = clean_room_text(profile_settings.get("effort"), limit=64)
    approval_policy = _codex_approval_policy(profile_settings.get("permissions"))
    sandbox_policy = _codex_app_server_sandbox_policy(profile_settings.get("sandbox"))
    if workspace:
        params["cwd"] = workspace
    if model:
        params["model"] = model
    if effort:
        params["effort"] = effort
    if approval_policy:
        params["approvalPolicy"] = approval_policy
    if sandbox_policy:
        params["sandboxPolicy"] = sandbox_policy
    return params


def _codex_app_server_sandbox_policy(value: object) -> dict[str, object]:
    sandbox = clean_room_text(value, limit=64)
    if sandbox == "read-only":
        return {"type": "readOnly", "networkAccess": False}
    if sandbox == "workspace-write":
        return {"type": "workspaceWrite", "networkAccess": False, "writableRoots": []}
    if sandbox == "danger-full-access":
        return {"type": "dangerFullAccess"}
    return {}


def _codex_approval_policy(value: object) -> str:
    permissions = clean_room_text(value, limit=64)
    if permissions in {"untrusted", "on-failure", "on-request", "never"}:
        return permissions
    if permissions == "prompt":
        return "on-request"
    return ""


def _codex_toml_string_config(key: str, value: str) -> str:
    return f"{key}={json.dumps(value, ensure_ascii=True)}"


def _codex_workspace_trust_config(value: object) -> str:
    workspace = _codex_workspace_path(value)
    if not workspace:
        return ""
    # app-server does not request dynamic tools from an untrusted cwd and can
    # otherwise leave the turn waiting indefinitely. Keep this trust grant
    # process-local instead of modifying the user's Codex configuration.
    project_key = f"projects.{json.dumps(workspace, ensure_ascii=True)}.trust_level"
    return _codex_toml_string_config(project_key, "trusted")


def _codex_workspace_path(value: object) -> str:
    workspace = clean_room_text(value, limit=300)
    if not workspace:
        return ""
    return str(Path(workspace).expanduser().resolve())


def _agent_turn_timeout_seconds(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return DEFAULT_AGENT_TURN_TIMEOUT_SECONDS
    if parsed <= 0:
        return DEFAULT_AGENT_TURN_TIMEOUT_SECONDS
    return min(parsed, DEFAULT_AGENT_TURN_TIMEOUT_SECONDS)


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _elapsed_ms(started_monotonic: float) -> int:
    return int((time.monotonic() - started_monotonic) * 1000)


def _earlier_deadline(*deadlines: float | None) -> float | None:
    active = [deadline for deadline in deadlines if deadline is not None]
    return min(active) if active else None

def _diagnostic_items(state: dict[str, object]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for key, value in state.items():
        if value in (None, "", [], {}):
            continue
        safe_value = redact_persisted_diagnostic_value(value)
        items.append({"setting": str(key), "status": str(safe_value), "message": str(safe_value)})
    return items


def _context_error_detected(values: object) -> bool:
    text = str(values).lower()
    return "context window" in text or "ran out of room" in text or "context_length" in text


def _app_server_progress_text(params: dict[str, object], *, completed: bool) -> str:
    return str(_app_server_activity(params, completed=completed).get("content") or "")


def _app_server_activity(
    params: dict[str, object],
    *,
    completed: bool,
) -> dict[str, str]:
    item = params.get("item") if isinstance(params.get("item"), dict) else {}
    item_type = clean_room_text(item.get("type"), limit=64)
    status = _app_server_activity_status(item, completed=completed)
    activity_id = clean_room_text(
        item.get("id") or params.get("itemId") or params.get("item_id"),
        limit=128,
    )
    if item_type == "reasoning":
        detail = safe_activity_detail(_app_server_reasoning_detail(item))
        return {
            "category": "reasoning",
            "status": status,
            "activity_id": activity_id or "reasoning",
            "activity_title": "생각",
            "activity_detail": detail,
            "content": (
                f"Thinking: {detail}"
                if detail
                else ("Thinking finished." if completed else "Thinking.")
            ),
        }
    if item_type in {"commandExecution", "command"}:
        raw_command = _app_server_command_detail(
            item.get("command") or item.get("cmd") or item.get("name")
        )
        command = safe_activity_detail(raw_command)
        display_command = safe_activity_display_detail(raw_command)
        return {
            "category": "command",
            "status": status,
            "activity_id": activity_id,
            "activity_title": "명령",
            "activity_detail": display_command,
            "content": (
                f"Tool finished: {command}" if completed else f"Using tool: {command}"
            )
            if command
            else ("Tool finished." if completed else "Using tool."),
        }
    if item_type in {"mcpToolCall", "toolCall"}:
        name = clean_room_text(item.get("name") or item.get("toolName"), limit=120)
        detail = safe_activity_display_detail(
            item.get("arguments") or item.get("input") or "",
            limit=600,
        )
        return {
            "category": "tool",
            "status": status,
            "activity_id": activity_id,
            "activity_title": name or "Tool",
            "activity_detail": detail,
            "content": detail
            or (
                (f"Tool finished: {name}" if completed else f"Using tool: {name}")
                if name
                else ("Tool finished." if completed else "Using tool.")
            ),
        }
    return {}


def _app_server_reasoning_detail(item: dict[str, object]) -> str:
    value = item.get("summary")
    if isinstance(value, str):
        return clean_room_text(value, limit=600)
    if isinstance(value, list):
        parts = [
            clean_room_text(
                entry.get("text") if isinstance(entry, dict) else entry,
                limit=300,
            )
            for entry in value
        ]
        return clean_room_text(" ".join(part for part in parts if part), limit=600)
    return ""


def _app_server_command_detail(value: object) -> str:
    if isinstance(value, (list, tuple)):
        parts = [clean_room_text(part, limit=300) for part in value]
        parts = [part for part in parts if part]
        if not parts:
            return ""
        parts[0] = Path(parts[0]).name
        return clean_room_text(shlex.join(parts), limit=600)
    return clean_room_text(value, limit=600)


def _app_server_activity_status(
    item: dict[str, object],
    *,
    completed: bool,
) -> str:
    raw_status = clean_room_text(item.get("status"), limit=32).casefold()
    if raw_status in {"cancelled", "canceled"}:
        return "cancelled"
    if raw_status in {"error", "failed"}:
        return "failed"
    exit_code = item.get("exitCode", item.get("exit_code"))
    if completed and isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0:
        return "failed"
    return "completed" if completed else "running"


def clean_provider_session_id(value: object) -> str:
    provider_session_id = clean_room_text(value, limit=200)
    if provider_session_id == "--last":
        return ""
    return provider_session_id


def _app_server_message_thread_id(message: dict[str, object]) -> str:
    return clean_provider_session_id(
        _nested_get(message, "params.threadId")
        or _nested_get(message, "params.thread.id")
        or _nested_get(message, "params.thread_id")
        or _nested_get(message, "params.item.threadId")
    )


def _app_server_message_turn_id(message: dict[str, object]) -> str:
    return clean_provider_session_id(
        _nested_get(message, "params.turnId")
        or _nested_get(message, "params.turn.id")
        or _nested_get(message, "params.turn_id")
        or _nested_get(message, "params.item.turnId")
    )


def _app_server_message_thread_status(message: dict[str, object]) -> str:
    return clean_room_text(
        _nested_get(message, "params.thread.status")
        or _nested_get(message, "params.status")
        or _nested_get(message, "params.threadStatus"),
        limit=128,
    )


def _app_server_message_turn_status(message: dict[str, object]) -> str:
    return clean_room_text(
        _nested_get(message, "params.turn.status")
        or _nested_get(message, "params.turnStatus"),
        limit=128,
    )


def _app_server_agent_message_completed(message: dict[str, object]) -> bool:
    method = clean_room_text(message.get("method"), limit=128)
    if method not in {
        "agent_message/completed",
        "agent-message/completed",
        "item/agent_message/completed",
        "item/completed",
    }:
        return False
    if method == "item/completed":
        return clean_room_text(_nested_get(message, "params.item.type"), limit=64) == "agentMessage"
    return True


def _is_agentsassemble_room_mcp_approval(message: dict[str, object]) -> bool:
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    metadata = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    return (
        clean_room_text(params.get("serverName"), limit=128)
        == "agentsassemble_room"
        and clean_room_text(metadata.get("codex_approval_kind"), limit=128)
        == "mcp_tool_call"
    )


def _app_server_thread_idle(message: dict[str, object]) -> bool:
    if clean_room_text(message.get("method"), limit=128) != "thread/status/changed":
        return False
    status = _nested_get(message, "params.thread.status") or _nested_get(message, "params.status")
    if isinstance(status, dict):
        return clean_room_text(status.get("type"), limit=64) == "idle"
    return clean_room_text(status, limit=128) == "idle"


def _nested_get(value: object, path: str) -> object:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict):
            return ""
        current = current.get(part)
    return current


def _codex_app_server_observed_model(message: dict[str, object]) -> str:
    method = clean_room_text(message.get("method"), limit=128)
    paths = (
        ("params.toModel",) if method == "model/rerouted" else ()
    ) + (
        "result.model",
        "result.thread.model",
        "result.turn.model",
        "params.model",
        "params.thread.model",
        "params.turn.model",
    )
    for path in paths:
        model = clean_room_text(_nested_get(message, path), limit=128)
        if model:
            return model
    return ""
