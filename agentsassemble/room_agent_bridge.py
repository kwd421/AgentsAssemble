from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from agentsassemble.grok_acp_runtime import GrokAcpRuntime
from agentsassemble.deepseek_runtime import DeepSeekApiRuntime
from agentsassemble.live_cli import LiveCliRuntime
from agentsassemble.opencode_runtime import OpenCodeRuntime
from agentsassemble.meeting_events import clean_lobby_text, has_room_visible_text
from agentsassemble.provider_runtime_contracts import (
    AdapterContractError,
    ProviderRuntimeHealth,
    ProviderTurnResult,
)
from agentsassemble.ws_room_client import WsRoomClient, connect_room_ws_with_ticket
from agentsassemble.windows_conpty import WindowsConPtyRuntime


class BridgeConfigError(ValueError):
    def __init__(self, message: str, *, code: str = "bridge_config_invalid") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CanonicalBridgeLaunchConfig:
    room_id: str
    participant_id: str
    session_id: str
    provider_kind: str
    command: tuple[str, ...]
    cwd: str
    model: str
    reasoning_effort: str
    service_tier: str
    variant: str
    permission_mode: str
    transport: str
    quiet_seconds: float
    input_mode: str
    submit_newline: str
    submit_delay_seconds: float
    terminal_rows: int
    terminal_columns: int
    startup_quiet_seconds: float
    startup_timeout_seconds: float
    startup_accept_contains: str
    startup_accept_keys: str
    startup_input: str
    turn_timeout_seconds: float
    runtime_profile_key: str
    runtime_state_dir: str
    credential_stdin: bool
    provider_endpoint: str
    provider_server_pid: int | None

    @classmethod
    def parse_strict(cls, values: dict[str, object]) -> CanonicalBridgeLaunchConfig:
        command_value = _required_value(values, "command")
        if not isinstance(command_value, list) or not command_value:
            raise BridgeConfigError("Agent Bridge command must be a non-empty list.")
        command = tuple(str(part) for part in command_value)
        if not command[0].strip():
            raise BridgeConfigError("Agent Bridge executable is required.")
        provider_kind = _required_text(values, "provider_kind", limit=64)
        provider_endpoint = _required_text(values, "provider_endpoint", limit=1000, allow_empty=True)
        if provider_kind == "opencode_server" and not provider_endpoint:
            raise BridgeConfigError("OpenCode Agent Bridge provider endpoint is required.")
        return cls(
            room_id=_required_text(values, "room_id", limit=128),
            participant_id=_required_text(values, "participant_id", limit=128),
            session_id=_required_text(values, "session_id", limit=128),
            provider_kind=provider_kind,
            command=command,
            cwd=_required_text(values, "cwd", limit=500),
            model=_required_text(values, "model", limit=256),
            reasoning_effort=_required_text(values, "reasoning_effort", limit=32, allow_empty=True),
            service_tier=_required_text(values, "service_tier", limit=32, allow_empty=True),
            variant=_required_text(values, "variant", limit=64, allow_empty=True),
            permission_mode=_required_text(values, "permission_mode", limit=64),
            transport=_required_text(values, "transport", limit=64),
            quiet_seconds=_required_float(values, "quiet_seconds", minimum=0.001),
            input_mode=_required_text(values, "input_mode", limit=64),
            submit_newline=_required_raw_text(values, "submit_newline", limit=16),
            submit_delay_seconds=_required_float(values, "submit_delay_seconds", minimum=0.0),
            terminal_rows=_required_int(values, "terminal_rows", minimum=1),
            terminal_columns=_required_int(values, "terminal_columns", minimum=1),
            startup_quiet_seconds=_required_float(values, "startup_quiet_seconds", minimum=0.0),
            startup_timeout_seconds=_required_float(values, "startup_timeout_seconds", minimum=0.001),
            startup_accept_contains=_required_raw_text(
                values, "startup_accept_contains", limit=1000, allow_empty=True
            ),
            startup_accept_keys=_required_raw_text(values, "startup_accept_keys", limit=1000, allow_empty=True),
            startup_input=_required_raw_text(values, "startup_input", limit=4000, allow_empty=True),
            turn_timeout_seconds=_required_float(values, "turn_timeout_seconds", minimum=0.001),
            runtime_profile_key=_required_text(values, "runtime_profile_key", limit=256),
            runtime_state_dir=_required_text(values, "runtime_state_dir", limit=1000),
            credential_stdin=_required_bool(values, "credential_stdin"),
            provider_endpoint=provider_endpoint,
            provider_server_pid=_required_optional_int(values, "provider_server_pid"),
        )


class BridgeRuntime(Protocol):
    def start(self) -> dict[str, object]: ...
    def send(self, text: str) -> None: ...
    def read_output(self, *, timeout_seconds: float, on_delta=None, on_activity=None) -> dict[str, object]: ...
    def interrupt(self) -> None: ...
    def stop(self, *, timeout_seconds: float = 2.0) -> None: ...
    def health(self) -> dict[str, object]: ...


class RoomAgentBridge:
    """Own one persistent provider CLI and report it over the room WebSocket."""

    def __init__(
        self,
        client: WsRoomClient,
        runtime: BridgeRuntime,
        *,
        room_id: str,
        participant_id: str,
        session_id: str,
        receive_sleep_seconds: float = 0.05,
        initial_orientation: str = "",
        stop_runtime_on_exit: bool = True,
    ) -> None:
        self.client = client
        self.runtime = runtime
        self.room_id = clean_lobby_text(room_id, limit=128)
        self.participant_id = clean_lobby_text(participant_id, limit=128)
        self.session_id = clean_lobby_text(session_id, limit=128)
        self.receive_sleep_seconds = max(0.001, float(receive_sleep_seconds))
        self._initial_orientation = str(initial_orientation or "").strip()
        self._stop_runtime_on_exit = bool(stop_runtime_on_exit)
        self._stop = threading.Event()
        self._worker_lock = threading.RLock()
        self._worker: threading.Thread | None = None

    def run(self) -> int:
        try:
            health = self.runtime.start()
            self._command("bridge.ready", self._health_payload(health))
            while not self._stop.is_set() and not self.client.closed:
                messages = self.client.receive()
                if not messages:
                    self._stop.wait(self.receive_sleep_seconds)
                    continue
                for message in messages:
                    self._handle_message(message)
                    if self._stop.is_set():
                        break
            return 0
        finally:
            self._stop.set()
            if self._stop_runtime_on_exit:
                try:
                    self.runtime.stop(timeout_seconds=2.0)
                except Exception:
                    pass
            with self._worker_lock:
                worker = self._worker
            if worker is not None and worker is not threading.current_thread():
                worker.join(timeout=2.0)
            self.client.close()

    def stop(self) -> None:
        self._stop.set()

    def _handle_message(self, message: dict[str, object]) -> None:
        op = clean_lobby_text(message.get("op"), limit=64)
        if op == "turn.assign":
            self._start_turn(message)
            return
        if op != "agent.control":
            return
        action = clean_lobby_text(message.get("action"), limit=32)
        if action == "interrupt":
            try:
                self.runtime.interrupt()
            except Exception as error:
                try:
                    diagnostics = self._health_payload(self.runtime.health())
                except AdapterContractError:
                    self._stop.set()
                    return
                self._command("bridge.health", {**diagnostics, "last_error": str(error)})
            return
        if action == "stop":
            self._stop.set()

    def _start_turn(self, assignment: dict[str, object]) -> None:
        turn_id = clean_lobby_text(assignment.get("turn_id"), limit=128)
        provider_input = str(assignment.get("provider_input") or "")
        if self._initial_orientation:
            provider_input = f"{self._initial_orientation}\n\n{provider_input}".strip()
            self._initial_orientation = ""
        if not turn_id or not provider_input:
            return
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                self._command(
                    "turn.failed",
                    {"turn_id": turn_id, "status": "error", "message": "Agent Bridge received a turn while busy."},
                )
                return
            turn_assignment = {**assignment, "provider_input": provider_input}
            self._worker = threading.Thread(
                target=self._run_turn,
                args=(turn_assignment,),
                name=f"AgentsAssembleBridgeTurn-{self.participant_id}",
                daemon=True,
            )
            self._worker.start()

    def _run_turn(self, assignment: dict[str, object]) -> None:
        turn_id = clean_lobby_text(assignment.get("turn_id"), limit=128)
        provider_input = str(assignment.get("provider_input") or "")
        timeout_seconds = _positive_float(assignment.get("timeout_seconds"), 180.0)
        started = time.monotonic()
        input_started_at = _now()
        first_output_at = ""
        first_output_elapsed: float | None = None
        last_output_at = ""
        delta_count = 0
        try:
            self.runtime.send(provider_input)
            input_completed_at = _now()
            input_completed = time.monotonic()
            self._command(
                "turn.state",
                {
                    "turn_id": turn_id,
                    "phase": "thinking",
                    "latency": {
                        "input_write_started_at": input_started_at,
                        "input_write_completed_at": input_completed_at,
                        "input_write_ms": round((input_completed - started) * 1000, 1),
                    },
                },
            )

            def on_delta(delta: str) -> None:
                nonlocal first_output_at, first_output_elapsed, last_output_at, delta_count
                content = str(delta or "")
                if not has_room_visible_text(content):
                    return
                now_mono = time.monotonic()
                now_iso = _now()
                if first_output_elapsed is None:
                    first_output_elapsed = now_mono
                    first_output_at = now_iso
                last_output_at = now_iso
                delta_count += 1
                self._command(
                    "message.delta",
                    {
                        "turn_id": turn_id,
                        "content": content,
                        "latency": {
                            "first_output_at": first_output_at,
                            "last_output_at": last_output_at,
                            "ttfo_ms": round((first_output_elapsed - input_completed) * 1000, 1),
                        },
                    },
                )

            def on_activity(activity: dict[str, object]) -> None:
                safe = _safe_activity(activity)
                if not safe:
                    return
                self._command("activity.update", {"turn_id": turn_id, **safe})

            raw_result = self.runtime.read_output(
                timeout_seconds=timeout_seconds,
                on_delta=on_delta,
                on_activity=on_activity,
            )
            result = ProviderTurnResult.parse(raw_result)
            if result.outcome == "decline":
                self._command(
                    "turn.decline",
                    {
                        "turn_id": turn_id,
                        "reason_code": result.decline_reason,
                        "diagnostics": self._health_payload(self.runtime.health()),
                    },
                )
                return
            final_content = _room_message_text(result.content, limit=12000)
            completed = time.monotonic()
            completed_at = _now()
            self._command(
                "message.final",
                {
                    "turn_id": turn_id,
                    "content": final_content,
                    "message_source": result.metadata.get("message_source")
                    or result.metadata.get("source_kind")
                    or "terminal",
                    "diagnostics": self._health_payload(self.runtime.health()),
                    "latency": {
                        "first_output_at": first_output_at,
                        "last_output_at": last_output_at or completed_at,
                        "quiet_detected_at": completed_at,
                        "turn_completed_at": completed_at,
                        "ttfo_ms": round((first_output_elapsed - input_completed) * 1000, 1)
                        if first_output_elapsed is not None
                        else None,
                        "total_turn_ms": round((completed - started) * 1000, 1),
                        "delta_count": delta_count,
                    },
                },
            )
        except Exception as error:
            if not self._stop.is_set():
                self._command(
                    "turn.failed",
                    {
                        "turn_id": turn_id,
                        "status": "error",
                        "error_code": getattr(error, "code", "provider_turn_failed"),
                        "message": str(error),
                        "diagnostics": self._failure_diagnostics(),
                    },
                )
        finally:
            with self._worker_lock:
                if self._worker is threading.current_thread():
                    self._worker = None

    def _command(self, action: str, payload: dict[str, object]) -> None:
        self.client.command(action, payload, request_id=f"bridge-{uuid4().hex[:20]}")

    def _failure_diagnostics(self) -> dict[str, object]:
        try:
            return self._health_payload(self.runtime.health())
        except AdapterContractError as error:
            return {"adapter_health_invalid": True, "adapter_contract_error": str(error)}

    def _health_payload(self, health: dict[str, object]) -> dict[str, object]:
        parsed = ProviderRuntimeHealth.parse(health)
        details = parsed.details
        return {
            "room_id": self.room_id,
            "participant_id": self.participant_id,
            "session_id": self.session_id,
            "pid": details.get("pid"),
            "running": parsed.running,
            "pty": parsed.pty,
            "transport": parsed.transport,
            "is_one_shot": bool(details.get("is_one_shot", False)),
            "resolved_executable": details.get("resolved_executable") or "",
            "started_at": parsed.started_at,
            "last_error": details.get("last_error") or "",
            "returncode": details.get("returncode"),
            "terminal_byte_count": int(details.get("terminal_byte_count") or 0),
            "terminal_tail": str(details.get("terminal_tail") or "")[-16000:],
            "stderr_drained": bool(details.get("stderr_drained", False)),
            "stderr_byte_count": int(details.get("stderr_byte_count") or 0),
            "stderr_line_count": int(details.get("stderr_line_count") or 0),
            "stderr_warning_count": int(details.get("stderr_warning_count") or 0),
            "stderr_tail": str(details.get("stderr_tail") or "")[-16000:],
            "stderr_tail_truncated": bool(details.get("stderr_tail_truncated", False)),
            "stderr_last_line_at": str(details.get("stderr_last_line_at") or ""),
            "provider_session_active": parsed.provider_session_active,
            "provider_session_load_supported": bool(details.get("provider_session_load_supported", False)),
            "provider_session_reused": bool(details.get("provider_session_reused", False)),
            "provider_session_resume_failed": bool(details.get("provider_session_resume_failed", False)),
            "provider_session_resume_error": str(details.get("provider_session_resume_error") or "")[:1000],
            "approval_policy": str(details.get("approval_policy") or ""),
            "yolo_mode": details.get("yolo_mode"),
            "permission_request_count": int(details.get("permission_request_count") or 0),
            "permission_denied_count": int(details.get("permission_denied_count") or 0),
            "empty_turn_recovery_count": int(details.get("empty_turn_recovery_count") or 0),
            "notification_drop_count": int(details.get("notification_drop_count") or 0),
            "message_source": str(details.get("message_source") or ""),
            "message_source_strict": bool(details.get("message_source_strict", False)),
            "model": str(details.get("model") or ""),
            "reasoning_effort": str(details.get("reasoning_effort") or ""),
            "service_tier": str(details.get("service_tier") or ""),
            "variant": str(details.get("variant") or ""),
            "permission_mode": str(details.get("permission_mode") or ""),
        }


def runtime_from_config(
    config: dict[str, object] | CanonicalBridgeLaunchConfig,
    *,
    credential: str = "",
) -> BridgeRuntime:
    launch = config if isinstance(config, CanonicalBridgeLaunchConfig) else CanonicalBridgeLaunchConfig.parse_strict(config)
    command = list(launch.command)
    provider_kind = launch.provider_kind
    if provider_kind == "deepseek_api":
        return DeepSeekApiRuntime(
            launch.participant_id,
            api_key=credential,
            model=launch.model,
            reasoning_effort=launch.reasoning_effort,
            thinking=launch.variant != "non_thinking",
        )
    if provider_kind == "opencode_server":
        return OpenCodeRuntime(
            launch.participant_id,
            endpoint=launch.provider_endpoint,
            workspace=launch.cwd,
            state_dir=launch.runtime_state_dir,
            model=launch.model,
            variant=launch.variant,
            permission_mode=launch.permission_mode,
            server_pid=launch.provider_server_pid,
        )
    if provider_kind == "grok_live_session" and _is_grok_acp_command(command):
        return GrokAcpRuntime(
            launch.participant_id,
            command,
            cwd=launch.cwd,
            state_dir=launch.runtime_state_dir,
            startup_timeout_seconds=launch.startup_timeout_seconds,
        )
    if provider_kind == "grok_live_session" and Path(command[0]).name.casefold() == "grok":
        raise ValueError("Grok Agent Sessions require grok agent stdio; PTY fallback is disabled.")
    runtime_class = WindowsConPtyRuntime if os.name == "nt" else LiveCliRuntime
    return runtime_class(
        launch.participant_id,
        command,
        cwd=launch.cwd,
        idle_quiet_seconds=launch.quiet_seconds,
        input_mode=launch.input_mode,
        submit_newline=launch.submit_newline,
        submit_delay_seconds=launch.submit_delay_seconds,
        terminal_rows=launch.terminal_rows,
        terminal_columns=launch.terminal_columns,
        startup_quiet_seconds=launch.startup_quiet_seconds,
        startup_timeout_seconds=launch.startup_timeout_seconds,
        startup_accept_contains=launch.startup_accept_contains,
        startup_accept_keys=launch.startup_accept_keys,
        startup_input=launch.startup_input,
        profile_settings={
            "model": launch.model,
            "reasoning_effort": launch.reasoning_effort,
            "service_tier": launch.service_tier,
            "variant": launch.variant,
            "permission_mode": launch.permission_mode,
        },
    )


def _is_grok_acp_command(command: list[str]) -> bool:
    executable = Path(command[0]).name.casefold() if command else ""
    parts = [str(part).casefold() for part in command[1:]]
    return executable == "grok" and "agent" in parts and "stdio" in parts


_ACTIVITY_LABELS = {
    "reasoning": {"started": "생각 정리 중", "running": "생각 정리 중", "completed": "생각 정리 완료"},
    "file_read": {"started": "파일 읽는 중", "running": "파일 읽는 중", "completed": "파일 확인 완료"},
    "search": {"started": "정보 검색 중", "running": "정보 검색 중", "completed": "정보 검색 완료"},
    "command": {"started": "명령 실행 중", "running": "명령 실행 중", "completed": "명령 실행 완료"},
    "web": {"started": "웹 확인 중", "running": "웹 확인 중", "completed": "웹 확인 완료"},
    "tool": {"started": "도구 사용 중", "running": "도구 사용 중", "completed": "도구 사용 완료"},
}


def _safe_activity(activity: object) -> dict[str, str]:
    values = activity if isinstance(activity, dict) else {}
    category = clean_lobby_text(values.get("category"), limit=32)
    status = clean_lobby_text(values.get("status"), limit=32)
    if category not in _ACTIVITY_LABELS:
        category = "tool"
    if status not in {"started", "running", "completed"}:
        status = "running"
    return {
        "activity_kind": "reasoning" if category == "reasoning" else "tool",
        "category": category,
        "status": status,
        "content": _ACTIVITY_LABELS[category][status],
    }


def _room_message_text(value: object, *, limit: int) -> str:
    return str(value or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")[:limit].strip()


def main() -> int:
    server_url = str(os.environ.get("AGENTSASSEMBLE_BRIDGE_SERVER_URL") or "")
    ticket = str(os.environ.get("AGENTSASSEMBLE_BRIDGE_TICKET") or "")
    config_path = Path(str(os.environ.get("AGENTSASSEMBLE_BRIDGE_CONFIG") or ""))
    if not server_url or not ticket or not config_path.is_file():
        raise SystemExit("Agent Bridge requires server URL, ticket, and config environment variables.")
    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw_config, dict):
        raise SystemExit("Agent Bridge config must be a JSON object.")
    config = CanonicalBridgeLaunchConfig.parse_strict(raw_config)
    credential = ""
    if config.credential_stdin:
        credential = sys.stdin.buffer.readline(16_384).decode("utf-8", errors="replace").strip()
        if not credential:
            raise SystemExit("Agent Bridge credential handoff was empty.")
    client = connect_room_ws_with_ticket(server_url, ticket, ["room_events"], timeout=10.0)
    try:
        client.sock.settimeout(0.25)
    except (AttributeError, OSError):
        pass
    bridge = RoomAgentBridge(
        client,
        runtime_from_config(config, credential=credential),
        room_id=config.room_id,
        participant_id=config.participant_id,
        session_id=config.session_id,
    )

    def stop_bridge(_signum, _frame) -> None:
        bridge.stop()

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop_bridge)
    return bridge.run()


def _positive_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _required_value(values: dict[str, object], key: str) -> object:
    if key not in values:
        raise BridgeConfigError(f"Agent Bridge config is missing {key}.")
    return values[key]


def _required_text(
    values: dict[str, object],
    key: str,
    *,
    limit: int,
    allow_empty: bool = False,
) -> str:
    value = clean_lobby_text(_required_value(values, key), limit=limit)
    if not value and not allow_empty:
        raise BridgeConfigError(f"Agent Bridge config {key} is required.")
    return value


def _required_raw_text(
    values: dict[str, object],
    key: str,
    *,
    limit: int,
    allow_empty: bool = False,
) -> str:
    value = _required_value(values, key)
    if not isinstance(value, str) or "\x00" in value or len(value) > limit:
        raise BridgeConfigError(f"Agent Bridge config {key} must be valid text up to {limit} characters.")
    if not value and not allow_empty:
        raise BridgeConfigError(f"Agent Bridge config {key} is required.")
    return value


def _required_float(values: dict[str, object], key: str, *, minimum: float) -> float:
    value = _required_value(values, key)
    if isinstance(value, bool):
        raise BridgeConfigError(f"Agent Bridge config {key} must be a number.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise BridgeConfigError(f"Agent Bridge config {key} must be a number.") from error
    if parsed < minimum:
        raise BridgeConfigError(f"Agent Bridge config {key} must be at least {minimum}.")
    return parsed


def _required_int(values: dict[str, object], key: str, *, minimum: int) -> int:
    value = _required_value(values, key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise BridgeConfigError(f"Agent Bridge config {key} must be an integer of at least {minimum}.")
    return value


def _required_bool(values: dict[str, object], key: str) -> bool:
    value = _required_value(values, key)
    if not isinstance(value, bool):
        raise BridgeConfigError(f"Agent Bridge config {key} must be a boolean.")
    return value


def _required_optional_int(values: dict[str, object], key: str) -> int | None:
    value = _required_value(values, key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BridgeConfigError(f"Agent Bridge config {key} must be a positive integer or null.")
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
