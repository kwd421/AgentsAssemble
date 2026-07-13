from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from agentsassemble.grok_acp_runtime import GrokAcpRuntime
from agentsassemble.deepseek_runtime import DeepSeekApiRuntime
from agentsassemble.live_cli import LiveCliRuntime
from agentsassemble.opencode_runtime import OpenCodeRuntime
from agentsassemble.meeting_events import clean_lobby_text, has_room_visible_text
from agentsassemble.ws_room_client import WsRoomClient, connect_room_ws_with_ticket
from agentsassemble.windows_conpty import WindowsConPtyRuntime


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
                self._command("bridge.health", {**self._health_payload(self.runtime.health()), "last_error": str(error)})
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

            result = self.runtime.read_output(
                timeout_seconds=timeout_seconds,
                on_delta=on_delta,
                on_activity=on_activity,
            )
            metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
            outcome = clean_lobby_text(result.get("outcome") or metadata.get("outcome"), limit=32)
            if outcome == "decline":
                self._command(
                    "turn.decline",
                    {
                        "turn_id": turn_id,
                        "reason_code": clean_lobby_text(
                            result.get("reason_code") or metadata.get("reason_code"), limit=64
                        )
                        or "nothing_useful_to_add",
                        "diagnostics": self._health_payload(self.runtime.health()),
                    },
                )
                return
            final_content = _room_message_text(result.get("content"), limit=12000)
            if not final_content:
                raise RuntimeError("Provider CLI completed without a clean assistant message.")
            completed = time.monotonic()
            completed_at = _now()
            self._command(
                "message.final",
                {
                    "turn_id": turn_id,
                    "content": final_content,
                    "message_source": metadata.get("message_source") or metadata.get("source_kind") or "terminal",
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
            health = self.runtime.health()
            if not self._stop.is_set():
                self._command(
                    "turn.failed",
                    {
                        "turn_id": turn_id,
                        "status": "error",
                        "message": str(error),
                        "diagnostics": self._health_payload(health),
                    },
                )
        finally:
            with self._worker_lock:
                if self._worker is threading.current_thread():
                    self._worker = None

    def _command(self, action: str, payload: dict[str, object]) -> None:
        self.client.command(action, payload, request_id=f"bridge-{uuid4().hex[:20]}")

    def _health_payload(self, health: dict[str, object]) -> dict[str, object]:
        return {
            "room_id": self.room_id,
            "participant_id": self.participant_id,
            "session_id": self.session_id,
            "pid": health.get("pid"),
            "running": bool(health.get("running", True)),
            "pty": bool(health.get("pty", True)),
            "transport": health.get("transport") or "pty",
            "is_one_shot": bool(health.get("is_one_shot", False)),
            "resolved_executable": health.get("resolved_executable") or "",
            "started_at": health.get("started_at") or _now(),
            "last_error": health.get("last_error") or "",
            "returncode": health.get("returncode"),
            "terminal_byte_count": int(health.get("terminal_byte_count") or 0),
            "terminal_tail": str(health.get("terminal_tail") or "")[-16000:],
            "stderr_drained": bool(health.get("stderr_drained", False)),
            "stderr_byte_count": int(health.get("stderr_byte_count") or 0),
            "stderr_line_count": int(health.get("stderr_line_count") or 0),
            "stderr_warning_count": int(health.get("stderr_warning_count") or 0),
            "stderr_tail": str(health.get("stderr_tail") or "")[-16000:],
            "stderr_tail_truncated": bool(health.get("stderr_tail_truncated", False)),
            "stderr_last_line_at": str(health.get("stderr_last_line_at") or ""),
            "provider_session_active": bool(health.get("provider_session_active", False)),
            "provider_session_load_supported": bool(health.get("provider_session_load_supported", False)),
            "provider_session_reused": bool(health.get("provider_session_reused", False)),
            "provider_session_resume_failed": bool(health.get("provider_session_resume_failed", False)),
            "provider_session_resume_error": str(health.get("provider_session_resume_error") or "")[:1000],
            "approval_policy": str(health.get("approval_policy") or ""),
            "yolo_mode": health.get("yolo_mode"),
            "permission_request_count": int(health.get("permission_request_count") or 0),
            "permission_denied_count": int(health.get("permission_denied_count") or 0),
            "empty_turn_recovery_count": int(health.get("empty_turn_recovery_count") or 0),
            "notification_drop_count": int(health.get("notification_drop_count") or 0),
            "message_source": str(health.get("message_source") or ""),
            "message_source_strict": bool(health.get("message_source_strict", False)),
            "model": str(health.get("model") or ""),
            "reasoning_effort": str(health.get("reasoning_effort") or ""),
            "service_tier": str(health.get("service_tier") or ""),
            "variant": str(health.get("variant") or ""),
            "permission_mode": str(health.get("permission_mode") or ""),
        }


def runtime_from_config(config: dict[str, object], *, credential: str = "") -> BridgeRuntime:
    command = [str(part) for part in config.get("command", [])] if isinstance(config.get("command"), list) else []
    if not command or not command[0].strip():
        raise ValueError("Agent Bridge command is required.")
    provider_kind = clean_lobby_text(config.get("provider_kind"), limit=64)
    if provider_kind == "deepseek_api":
        return DeepSeekApiRuntime(
            clean_lobby_text(config.get("participant_id") or config.get("agent_id"), limit=128),
            api_key=credential,
            model=clean_lobby_text(config.get("model"), limit=128) or "deepseek-v4-flash",
            reasoning_effort=clean_lobby_text(config.get("reasoning_effort"), limit=32) or "high",
            thinking=clean_lobby_text(config.get("variant"), limit=32) != "non_thinking",
        )
    if provider_kind == "opencode_server":
        return OpenCodeRuntime(
            clean_lobby_text(config.get("participant_id") or config.get("agent_id"), limit=128),
            endpoint=clean_lobby_text(config.get("provider_endpoint"), limit=1000),
            workspace=clean_lobby_text(config.get("cwd"), limit=500) or ".",
            state_dir=clean_lobby_text(config.get("runtime_state_dir"), limit=1000)
            or ".agentsassemble/opencode",
            model=clean_lobby_text(config.get("model"), limit=256) or "opencode-go/glm-5.2",
            variant=clean_lobby_text(config.get("variant"), limit=64),
            permission_mode=clean_lobby_text(config.get("permission_mode"), limit=64)
            or "meeting_read_only",
            server_pid=_optional_int(config.get("provider_server_pid")),
        )
    if provider_kind == "grok_live_session" and _is_grok_acp_command(command):
        return GrokAcpRuntime(
            clean_lobby_text(config.get("participant_id") or config.get("agent_id"), limit=128),
            command,
            cwd=clean_lobby_text(config.get("cwd"), limit=500) or ".",
            state_dir=clean_lobby_text(config.get("runtime_state_dir"), limit=1000)
            or ".agentsassemble/grok-acp",
            startup_timeout_seconds=_positive_float(config.get("startup_timeout_seconds"), 20.0),
        )
    if provider_kind == "grok_live_session" and Path(command[0]).name.casefold() == "grok":
        raise ValueError("Grok Agent Sessions require grok agent stdio; PTY fallback is disabled.")
    runtime_class = WindowsConPtyRuntime if os.name == "nt" else LiveCliRuntime
    return runtime_class(
        clean_lobby_text(config.get("participant_id") or config.get("agent_id"), limit=128),
        command,
        cwd=clean_lobby_text(config.get("cwd"), limit=500) or None,
        idle_quiet_seconds=_positive_float(config.get("quiet_seconds"), 4.0),
        input_mode=clean_lobby_text(config.get("input_mode"), limit=64) or "line",
        submit_newline=str(config.get("submit_newline") or "\r"),
        submit_delay_seconds=_nonnegative_float(config.get("submit_delay_seconds"), 0.1),
        terminal_rows=int(config.get("terminal_rows") or 40),
        terminal_columns=int(config.get("terminal_columns") or 120),
        startup_quiet_seconds=_nonnegative_float(config.get("startup_quiet_seconds"), 1.0),
        startup_timeout_seconds=_nonnegative_float(config.get("startup_timeout_seconds"), 20.0),
        startup_accept_contains=str(config.get("startup_accept_contains") or ""),
        startup_accept_keys=str(config.get("startup_accept_keys") or "\r"),
        startup_input=str(config.get("startup_input") or ""),
        profile_settings={
            "model": config.get("model"),
            "reasoning_effort": config.get("reasoning_effort"),
            "service_tier": config.get("service_tier"),
            "variant": config.get("variant"),
            "permission_mode": config.get("permission_mode"),
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
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise SystemExit("Agent Bridge config must be a JSON object.")
    credential = ""
    if bool(config.get("credential_stdin")):
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
        room_id=str(config.get("room_id") or "general"),
        participant_id=str(config.get("participant_id") or ""),
        session_id=str(config.get("session_id") or config.get("participant_id") or ""),
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


def _nonnegative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _optional_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _now() -> str:
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
