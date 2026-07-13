from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from agentsassemble.bridge_protocol import (
    BridgeProtocolError,
    BridgeReportRejected,
    BridgeReportTimeout,
    TurnAssignmentEnvelope,
)
from agentsassemble.bridge_report_tracker import BridgeReportTracker
from agentsassemble.cleanup_report import CleanupReport, emit_cleanup_failure
from agentsassemble.meeting_events import clean_lobby_text, has_room_visible_text
from agentsassemble.provider_runtime_contracts import (
    AdapterContractError,
    ProviderRuntimeHealth,
    ProviderTurnResult,
)
from agentsassemble.provider_runtime_config import (
    BridgeConfigError,
    CanonicalBridgeLaunchConfig,
    ProviderRuntimeConfig,
    ProviderRuntimeProfile,
)
from agentsassemble.provider_runtime_factory import runtime_from_config
from agentsassemble.ws_room_client import WsRoomClient, connect_room_ws_with_ticket


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
        report_timeout_seconds: float = 5.0,
        runtime_profile: ProviderRuntimeProfile | None = None,
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
        self._report_tracker = BridgeReportTracker(timeout_seconds=report_timeout_seconds)
        self._runtime_profile = runtime_profile
        self._diagnostics_lock = threading.RLock()
        self._activity_invalid_count = 0
        self._run_thread: threading.Thread | None = None
        self._last_observed_seq_reported = 0
        self.remote_stop_requested = False
        self.remote_stop_control_id = ""
        self.remote_stop_confirmation_required = False
        self.last_cleanup_report = CleanupReport("room_agent_bridge")

    def run(self) -> int:
        self._run_thread = threading.current_thread()
        try:
            health = self.runtime.start()
            self._command("bridge.ready", self._health_payload(health))
            while not self._stop.is_set() and not self.client.closed:
                messages = self.client.receive()
                if not messages:
                    self._stop.wait(self.receive_sleep_seconds)
                    continue
                observed_seq = _latest_room_event_seq(messages)
                for message in messages:
                    self._handle_message(message)
                    if self._stop.is_set():
                        break
                self._report_observed_seq(observed_seq)
        finally:
            self._stop.set()
            cleanup = CleanupReport("room_agent_bridge")
            if self._stop_runtime_on_exit or self.remote_stop_requested:
                try:
                    self.runtime.stop(timeout_seconds=2.0)
                    cleanup.record_success()
                except Exception as error:
                    cleanup.record_failure(
                        "runtime.stop",
                        error,
                        handle_id=self.session_id,
                        orphaned=_runtime_still_running(self.runtime),
                    )
            with self._worker_lock:
                worker = self._worker
            if worker is not None and worker is not threading.current_thread():
                worker.join(timeout=2.0)
                if worker.is_alive():
                    cleanup.record_failure(
                        "turn_worker.join",
                        RuntimeError("Turn worker did not stop before the cleanup deadline."),
                        handle_id=self.session_id,
                    )
                else:
                    cleanup.record_success()
            if self.remote_stop_confirmation_required:
                stopped = cleanup.ok and not _runtime_still_running(self.runtime)
                try:
                    self._command(
                        "bridge.stopped",
                        {
                            "control_id": self.remote_stop_control_id,
                            "stopped": stopped,
                            "error_code": "" if stopped else "runtime_stop_failed",
                            "message": "" if stopped else "Provider shutdown could not be confirmed.",
                        },
                        wait_for_ack=False,
                    )
                except Exception as error:
                    cleanup.record_failure(
                        "bridge.stopped",
                        error,
                        handle_id=self.session_id,
                    )
            try:
                self.client.close()
                cleanup.record_success()
            except Exception as error:
                cleanup.record_failure("websocket.close", error, handle_id=self.session_id)
            self.last_cleanup_report = cleanup
            emit_cleanup_failure(cleanup)
            self._run_thread = None
        return 0 if self.last_cleanup_report.ok else 1

    def stop(self) -> None:
        self._stop.set()

    def _handle_message(self, message: dict[str, object]) -> None:
        if self._report_tracker.resolve_message(message):
            return
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
            require_confirmation = message.get("require_confirmation") is True
            control_id = clean_lobby_text(message.get("control_id"), limit=128)
            if require_confirmation and not control_id:
                self._fail_protocol(
                    BridgeProtocolError(
                        "A confirmed stop requires control_id.",
                        code="stop_control_id_missing",
                        fatal=True,
                    )
                )
                return
            self.remote_stop_requested = True
            self.remote_stop_confirmation_required = require_confirmation
            self.remote_stop_control_id = control_id
            self._stop.set()

    def _start_turn(self, assignment: dict[str, object]) -> None:
        try:
            envelope = TurnAssignmentEnvelope.parse_strict(
                assignment,
                room_id=self.room_id,
                participant_id=self.participant_id,
                session_id=self.session_id,
            )
        except BridgeProtocolError as error:
            if error.fatal:
                self._fail_protocol(error)
                return
            self._command(
                "turn.failed",
                {
                    "turn_id": error.turn_id,
                    "status": "error",
                    "error_code": error.code,
                    "message": str(error),
                },
            )
            return
        if self._initial_orientation:
            envelope = replace(
                envelope,
                provider_input=f"{self._initial_orientation}\n\n{envelope.provider_input}".strip(),
            )
            self._initial_orientation = ""
        with self._worker_lock:
            current_worker = self._worker
        if current_worker is not None and current_worker.is_alive():
            current_worker.join(timeout=0.25)
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                self._command(
                    "turn.failed",
                    {
                        "turn_id": envelope.turn_id,
                        "status": "error",
                        "error_code": "bridge_busy",
                        "message": "Agent Bridge received a turn while busy.",
                    },
                )
                return
            self._worker = threading.Thread(
                target=self._run_turn,
                args=(envelope,),
                name=f"AgentsAssembleBridgeTurn-{self.participant_id}",
                daemon=True,
            )
            self._worker.start()

    def _run_turn(self, assignment: TurnAssignmentEnvelope) -> None:
        turn_id = assignment.turn_id
        provider_input = assignment.provider_input
        timeout_seconds = assignment.timeout_seconds
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
                wait_for_ack=False,
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
                    wait_for_ack=False,
                )

            def on_activity(activity: dict[str, object]) -> None:
                safe = _safe_activity(activity)
                if not safe:
                    with self._diagnostics_lock:
                        self._activity_invalid_count += 1
                    return
                self._command("activity.update", {"turn_id": turn_id, **safe}, wait_for_ack=False)

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
                    "observed_model_id": clean_lobby_text(
                        result.metadata.get("observed_model_id"),
                        limit=128,
                    ),
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
        except (BridgeReportRejected, BridgeReportTimeout) as report_error:
            print(
                f"Agent Bridge terminal report failed: {report_error.code}",
                file=sys.stderr,
                flush=True,
            )
            self._stop.set()
        except Exception as error:
            if not self._stop.is_set():
                try:
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
                except (BridgeReportRejected, BridgeReportTimeout) as report_error:
                    print(
                        f"Agent Bridge terminal report failed: {report_error.code}",
                        file=sys.stderr,
                        flush=True,
                    )
                    self._stop.set()
        finally:
            with self._worker_lock:
                if self._worker is threading.current_thread():
                    self._worker = None

    def _command(
        self,
        action: str,
        payload: dict[str, object],
        *,
        wait_for_ack: bool = True,
    ) -> dict[str, object] | None:
        if not wait_for_ack:
            request_id = self._report_tracker.new_request_id()
            self.client.command(action, payload, request_id=request_id)
            return None
        pump = self._pump_report_messages if threading.current_thread() is self._run_thread else None
        return self._report_tracker.request(
            action,
            send=lambda request_id: self.client.command(action, payload, request_id=request_id),
            pump=pump,
            is_closed=lambda: self.client.closed,
            wait_interval_seconds=self.receive_sleep_seconds,
        )

    def _pump_report_messages(self) -> bool:
        messages = self.client.receive()
        observed_seq = _latest_room_event_seq(messages)
        for message in messages:
            self._handle_message(message)
        self._report_observed_seq(observed_seq)
        return bool(messages)

    def _report_observed_seq(self, observed_seq: int) -> None:
        if observed_seq <= self._last_observed_seq_reported:
            return
        self._command("room.observed", {"through_seq": observed_seq}, wait_for_ack=False)
        self._last_observed_seq_reported = observed_seq

    def _fail_protocol(self, error: BridgeProtocolError) -> None:
        print(f"Agent Bridge protocol error: {error.code}", file=sys.stderr, flush=True)
        self._stop.set()
        self.client.close()

    def _failure_diagnostics(self) -> dict[str, object]:
        try:
            return self._health_payload(self.runtime.health())
        except AdapterContractError as error:
            return {"adapter_health_invalid": True, "adapter_contract_error": str(error)}

    def _health_payload(self, health: dict[str, object]) -> dict[str, object]:
        parsed = ProviderRuntimeHealth.parse(health)
        details = parsed.details
        runtime_kind = clean_lobby_text(details.get("runtime_kind"), limit=64)
        if self._runtime_profile is not None:
            if runtime_kind and runtime_kind != self._runtime_profile.runtime_kind:
                raise AdapterContractError(
                    "Provider runtime health reported a different runtime kind than its launch profile."
                )
            runtime_kind = self._runtime_profile.runtime_kind
        with self._diagnostics_lock:
            activity_invalid_count = self._activity_invalid_count
        payload = {
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
            "notification_drop_count": int(details.get("notification_drop_count") or 0),
            "adapter_activity_invalid_count": activity_invalid_count,
            "message_source": str(details.get("message_source") or ""),
            "message_source_strict": bool(details.get("message_source_strict", False)),
            "model": str(details.get("model") or ""),
            "reasoning_effort": str(details.get("reasoning_effort") or ""),
            "service_tier": str(details.get("service_tier") or ""),
            "variant": str(details.get("variant") or ""),
            "permission_mode": str(details.get("permission_mode") or ""),
            "runtime_kind": runtime_kind,
        }
        if self._runtime_profile is not None:
            payload.update(self._runtime_profile.report_fields())
        return payload


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
    if category not in _ACTIVITY_LABELS or status not in {"started", "running", "completed"}:
        return {}
    return {
        "activity_kind": "reasoning" if category == "reasoning" else "tool",
        "category": category,
        "status": status,
        "content": _ACTIVITY_LABELS[category][status],
    }


def _runtime_still_running(runtime: BridgeRuntime) -> bool:
    try:
        return bool(runtime.health().get("running", True))
    except Exception:
        return True


def _room_message_text(value: object, *, limit: int) -> str:
    return str(value or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")[:limit].strip()


def _latest_room_event_seq(messages: list[dict[str, object]]) -> int:
    latest = 0
    for message in messages:
        if (
            clean_lobby_text(message.get("op"), limit=32) not in {"event", "snapshot"}
            or clean_lobby_text(message.get("stream"), limit=32) != "room_events"
        ):
            continue
        events = message.get("events") if isinstance(message.get("events"), list) else []
        for event in events:
            if isinstance(event, dict):
                latest = max(latest, max(0, int(event.get("seq") or 0)))
    return latest


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
        runtime_from_config(config.runtime, credential=credential),
        room_id=config.room_id,
        participant_id=config.runtime.participant_id,
        session_id=config.session_id,
        runtime_profile=config.runtime.profile,
    )

    def stop_bridge(_signum, _frame) -> None:
        bridge.stop()

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop_bridge)
    return bridge.run()


def _now() -> str:
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
