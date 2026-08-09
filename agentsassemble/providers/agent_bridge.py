"""Persistent provider runtime bridge for the canonical room protocol."""

from __future__ import annotations

import base64
import sys
import threading
import time
from collections import deque
from datetime import UTC, datetime
from typing import Protocol

from agentsassemble.providers.bridge_protocol import (
    BridgeProtocolError,
    BridgeReportRejected,
    BridgeReportTimeout,
    RoomWakeEnvelope,
    TurnAssignmentEnvelope,
)
from agentsassemble.providers.bridge_report_tracker import BridgeReportTracker
from agentsassemble.providers.bridge_failure_reporting import (
    report_bridge_start_failure,
    report_failure_allows_reconnect,
    turn_failure_payload,
)
from agentsassemble.diagnostics.cleanup import CleanupReport, emit_cleanup_failure
from agentsassemble.room.text import (
    clean_room_text as clean_lobby_text,
    has_room_visible_text,
)
from agentsassemble.room.projection import (
    PUBLIC_ACTIVITY_STATUSES,
    public_activity,
    safe_activity_display_detail,
    safe_activity_detail,
    safe_activity_id,
)
from agentsassemble.providers.runtime_contracts import (
    AdapterContractError,
    BridgeRuntime,
    ProviderRuntimeHealth,
    ProviderTurnResult,
)
from agentsassemble.providers.runtime_config import ProviderRuntimeProfile
from agentsassemble.providers.room_portal import (
    RoomPortal,
    RoomPortalError,
    automatic_turn_orientation,
    room_wake_orientation,
)
from agentsassemble.providers.provider_requests import BridgeProviderRequestRouter


class BridgeRoomClient(Protocol):
    closed: bool

    def receive(self) -> list[dict[str, object]]: ...
    def command(
        self,
        action: str,
        payload: dict[str, object] | None = None,
        *,
        request_id: str = "",
    ) -> str: ...
    def close(self) -> None: ...


class RoomAgentBridge:
    """Own one persistent provider CLI and report it over the room WebSocket."""

    def __init__(
        self,
        client: BridgeRoomClient,
        runtime: BridgeRuntime,
        *,
        room_id: str,
        participant_id: str,
        session_id: str,
        bridge_launch_id: str = "",
        receive_sleep_seconds: float = 0.05,
        receive_timeout_seconds: float = 1.0,
        initial_orientation: str = "",
        stop_runtime_on_exit: bool = True,
        report_timeout_seconds: float = 5.0,
        runtime_profile: ProviderRuntimeProfile | None = None,
        room_portal: RoomPortal | None = None,
        idle_room_check_seconds: float = 300.0,
    ) -> None:
        self.client = client
        self.runtime = runtime
        self.room_id = clean_lobby_text(room_id, limit=128)
        self.participant_id = clean_lobby_text(participant_id, limit=128)
        self.session_id = clean_lobby_text(session_id, limit=128)
        self.bridge_launch_id = clean_lobby_text(bridge_launch_id, limit=128)
        self.receive_sleep_seconds = max(0.001, float(receive_sleep_seconds))
        self.receive_timeout_seconds = max(0.05, float(receive_timeout_seconds))
        self._initial_orientation = str(initial_orientation or "").strip()
        self._stop_runtime_on_exit = bool(stop_runtime_on_exit)
        self._stop = threading.Event()
        self._worker_lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._report_tracker = BridgeReportTracker(timeout_seconds=report_timeout_seconds)
        self._runtime_profile = runtime_profile
        self._room_portal = room_portal
        self._diagnostics_lock = threading.RLock()
        self._activity_invalid_count = 0
        self._run_thread: threading.Thread | None = None
        self._last_observed_seq_reported = 0
        self._deferred_messages: deque[dict[str, object]] = deque()
        self._idle_room_check_seconds = max(5.0, float(idle_room_check_seconds))
        self._next_idle_room_check_at = time.monotonic() + self._idle_room_check_seconds
        set_receive_timeout = getattr(self.client, "set_receive_timeout", None)
        if callable(set_receive_timeout):
            set_receive_timeout(self.receive_timeout_seconds)
        self.remote_stop_requested = False
        self.reconnect_permitted = True
        self.ready_reported = self.report_timeout_reconnect_requested = False
        self.remote_stop_control_id = ""
        self.remote_stop_confirmation_required = False
        self.last_cleanup_report = CleanupReport("room_agent_bridge")
        self._provider_requests = BridgeProviderRequestRouter(
            report=self._command,
            stopping=self._stop,
        )
        set_request_handler = getattr(self.runtime, "set_request_handler", None)
        if callable(set_request_handler):
            set_request_handler(self._provider_requests.handle)

    def run(self) -> int:
        self._run_thread = threading.current_thread()
        try:
            try:
                health = self.runtime.start()
            except Exception as error:
                self.reconnect_permitted = False
                report_bridge_start_failure(self._command, error)
                return 1
            try:
                self._command("bridge.ready", self._health_payload(health))
                self.ready_reported = True
            except (BridgeReportRejected, BridgeReportTimeout) as error:
                self._stop_after_report_failure(error, "ready")
                return 1
            while not self._stop.is_set() and not self.client.closed:
                messages = self._drain_deferred_messages()
                if not messages:
                    messages = self.client.receive()
                if not messages:
                    self._request_idle_room_check_if_due()
                    self._stop.wait(self.receive_sleep_seconds)
                    continue
                for message in messages:
                    self._handle_message(message)
                    if self._stop.is_set():
                        break
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
        self._ingest_room_frame(message)
        op = clean_lobby_text(message.get("op"), limit=64)
        if op == "room.wake":
            self._start_room_wake(message)
            return
        if op == "turn.assign":
            self._start_turn(message)
            return
        if op == "provider.request.resolve":
            if not self._provider_requests.resolve(message):
                try:
                    self._provider_requests.close_unmatched(message)
                except (BridgeReportRejected, BridgeReportTimeout) as error:
                    print(
                        f"Agent Bridge could not close an unmatched provider request: {error.code}",
                        file=sys.stderr,
                        flush=True,
                    )
                self._fail_protocol(
                    BridgeProtocolError(
                        "Provider request resolution did not match a pending request.",
                        code="provider_request_resolution_invalid",
                        fatal=False,
                    )
                )
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

    def _start_room_wake(self, wake: dict[str, object]) -> None:
        portal = self._room_portal
        if portal is None:
            self._command(
                "turn.failed",
                {
                    "turn_id": clean_lobby_text(wake.get("turn_id"), limit=128),
                    "status": "error",
                    "error_code": "room_portal_unavailable",
                    "message": "Autonomous room observation requires a private room portal.",
                },
            )
            return
        try:
            envelope = RoomWakeEnvelope.parse_strict(
                wake,
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
                        "message": "Agent Bridge received a room wake while busy.",
                    },
                )
                return
            try:
                portal.begin_observation(
                    envelope.turn_id,
                    attachment_ids=envelope.attachment_ids,
                    input_up_to_seq=envelope.input_up_to_seq,
                )
            except RoomPortalError as error:
                self._command(
                    "turn.failed",
                    {
                        "turn_id": envelope.turn_id,
                        "status": "error",
                        "error_code": "room_portal_failed",
                        "message": str(error),
                    },
                )
                return
            self._worker = threading.Thread(
                target=self._run_room_observation,
                args=(envelope,),
                name=f"AgentsAssembleBridgeObservation-{self.participant_id}",
                daemon=True,
            )
            self._worker.start()

    def _run_room_observation(self, wake: RoomWakeEnvelope) -> None:
        portal = self._room_portal
        if portal is None:
            return
        turn_id = wake.turn_id
        provider_input = self._with_initial_orientation(
            (
                f"{room_wake_orientation(self._provider_kind(), observation_kind=wake.observation_kind, tool_names=portal.active_tool_names())}"
                f"\n\nroom.wake {turn_id}"
            )
        )
        started = time.monotonic()
        input_started_at = _now()
        first_output_at = ""
        first_output_elapsed: float | None = None
        last_output_at = ""
        delta_count = 0
        try:
            send_observation = getattr(self.runtime, "send_room_observation", None)
            if callable(send_observation):
                send_observation(
                    provider_input,
                    media_blocks=portal.native_media_blocks(),
                )
            else:
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

            def on_private_delta(delta: str) -> None:
                nonlocal first_output_at, first_output_elapsed, last_output_at, delta_count
                if not str(delta or ""):
                    return
                now_mono = time.monotonic()
                now_iso = _now()
                if first_output_elapsed is None:
                    first_output_elapsed = now_mono
                    first_output_at = now_iso
                last_output_at = now_iso
                delta_count += 1

            def on_activity(activity: dict[str, object]) -> None:
                safe = _safe_activity(activity)
                if not safe:
                    with self._diagnostics_lock:
                        self._activity_invalid_count += 1
                    return
                self._command(
                    "activity.update",
                    {"turn_id": turn_id, **safe},
                    wait_for_ack=False,
                )

            try:
                raw_result = self.runtime.read_output(
                    timeout_seconds=wake.timeout_seconds,
                    on_delta=on_private_delta,
                    on_activity=on_activity,
                )
            except Exception as provider_error:
                try:
                    self._publish_observation_results(portal, turn_id)
                except Exception as publish_error:
                    print(
                        "Agent Bridge room result publication also failed "
                        f"({getattr(publish_error, 'code', type(publish_error).__name__)}); "
                        "preserving the provider output failure.",
                        file=sys.stderr,
                        flush=True,
                    )
                    if hasattr(provider_error, "add_note"):
                        provider_error.add_note(
                            "Room result publication also failed: "
                            f"{getattr(publish_error, 'code', type(publish_error).__name__)}"
                        )
                raise
            self._publish_observation_results(portal, turn_id)
            result = ProviderTurnResult.parse(raw_result)
            observed_through_seq = max(0, int(portal.observation_receipt(turn_id) or 0))
            if observed_through_seq < wake.input_up_to_seq:
                self._command(
                    "turn.failed",
                    {
                        "turn_id": turn_id,
                        "status": "error",
                        "error_code": "room_observation_unconfirmed",
                        "message": "Provider completed the room observation but did not read "
                        "the assigned room state through the RoomPortal.",
                        "diagnostics": self._failure_diagnostics(),
                    },
                )
                return
            self._report_observation_receipt(observed_through_seq)
            explicit_decline_reason = portal.observation_decline_reason(turn_id)
            publication = portal.publication_result(turn_id)
            completed = time.monotonic()
            completed_at = _now()
            latency = {
                "first_output_at": first_output_at,
                "last_output_at": last_output_at or completed_at,
                "quiet_detected_at": completed_at,
                "turn_completed_at": completed_at,
                "ttfo_ms": round((first_output_elapsed - input_completed) * 1000, 1)
                if first_output_elapsed is not None
                else None,
                "total_turn_ms": round((completed - started) * 1000, 1),
                "delta_count": delta_count,
            }
            if not publication.has_message:
                decline_reason = explicit_decline_reason or (
                    result.decline_reason
                    if result.outcome == "decline"
                    else "nothing_useful_to_add"
                )
                self._command(
                    "turn.decline",
                    {
                        "turn_id": turn_id,
                        "reason_code": decline_reason,
                        "diagnostics": self._health_payload(self.runtime.health()),
                        "latency": latency,
                        "observed_through_seq": observed_through_seq,
                    },
                )
                return
            self._command(
                "message.final",
                {
                    "turn_id": turn_id,
                    "observed_through_seq": observed_through_seq,
                    "observed_model_id": clean_lobby_text(
                        result.metadata.get("observed_model_id"),
                        limit=128,
                    ),
                    "diagnostics": self._health_payload(self.runtime.health()),
                    "latency": latency,
                },
            )
        except (BridgeReportRejected, BridgeReportTimeout) as report_error:
            self._stop_after_report_failure(report_error, "room observation")
        except Exception as error:
            self._report_turn_failure(turn_id, error, context="room observation")
        finally:
            portal.end_observation(turn_id)
            with self._worker_lock:
                if self._worker is threading.current_thread():
                    self._worker = None

    def _publish_observation_results(
        self,
        portal: RoomPortal,
        turn_id: str,
    ) -> None:
        batch = portal.observation_result_batch(turn_id)
        if batch.diagnostic_count:
            with self._diagnostics_lock:
                self._activity_invalid_count += batch.diagnostic_count
            print(
                "Agent Bridge bounded room result activity "
                f"(malformed={batch.malformed_count}, "
                f"capped={batch.capped_count}, "
                f"bytes_truncated={batch.bytes_truncated}).",
                file=sys.stderr,
                flush=True,
            )
        for room_result in batch.results:
            result_id = clean_lobby_text(room_result.get("result_id"), limit=64)
            self._command(
                "room.result.publish",
                {
                    "turn_id": turn_id,
                    "result_id": result_id,
                    "operation": room_result["operation"],
                    "details": room_result["details"],
                },
                request_id=f"bridge-room-result-{result_id}",
                retry_on_timeout=True,
            )

    def _run_turn(self, assignment: TurnAssignmentEnvelope) -> None:
        turn_id = assignment.turn_id
        provider_input = self._with_initial_orientation(
            f"{automatic_turn_orientation()}\n\n{assignment.provider_input}"
        )
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
            self._stop_after_report_failure(report_error, "terminal")
        except Exception as error:
            self._report_turn_failure(turn_id, error, context="terminal")
        finally:
            with self._worker_lock:
                if self._worker is threading.current_thread():
                    self._worker = None

    def _with_initial_orientation(self, provider_input: str) -> str:
        with self._worker_lock:
            orientation = self._initial_orientation
            self._initial_orientation = ""
        if not orientation:
            return provider_input
        return f"{orientation}\n\n{provider_input}".strip()

    def _provider_kind(self) -> str:
        if self._runtime_profile is None:
            return ""
        return clean_lobby_text(self._runtime_profile.provider_kind, limit=64)

    def _command(
        self,
        action: str,
        payload: dict[str, object],
        *,
        wait_for_ack: bool = True,
        request_id: str = "",
        retry_on_timeout: bool = False,
    ) -> dict[str, object] | None:
        if not wait_for_ack:
            request_id = request_id or self._report_tracker.new_request_id()
            self.client.command(action, payload, request_id=request_id)
            return None
        pump = self._pump_report_messages if threading.current_thread() is self._run_thread else None
        return self._report_tracker.request(
            action,
            send=lambda request_id: self.client.command(action, payload, request_id=request_id),
            pump=pump,
            is_closed=lambda: self.client.closed,
            wait_interval_seconds=self.receive_sleep_seconds,
            request_id=request_id,
            retry_on_timeout=retry_on_timeout,
        )

    def _pump_report_messages(self) -> bool:
        messages = self.client.receive()
        for message in messages:
            if not self._report_tracker.resolve_message(message):
                self._deferred_messages.append(message)
        return bool(messages)

    def _drain_deferred_messages(self) -> list[dict[str, object]]:
        messages = list(self._deferred_messages)
        self._deferred_messages.clear()
        return messages

    def _ingest_room_frame(self, message: dict[str, object]) -> None:
        portal = self._room_portal
        if portal is None:
            return
        attachments = portal.ingest_frame(message)
        if _contains_final_room_message(message):
            self._next_idle_room_check_at = time.monotonic() + self._idle_room_check_seconds
        for attachment in attachments:
            self._stage_attachment(attachment)

    def _stage_attachment(self, attachment: dict[str, object]) -> None:
        portal = self._room_portal
        if portal is None:
            return
        try:
            ack = self._command(
                "room.attachment.read",
                {"attachment_id": attachment.get("id")},
            )
            result = ack.get("result") if isinstance(ack, dict) and isinstance(ack.get("result"), dict) else {}
            metadata = result.get("attachment") if isinstance(result.get("attachment"), dict) else attachment
            encoded = result.get("data_base64")
            if not isinstance(encoded, str) or not encoded:
                raise RoomPortalError("Attachment response did not include content.")
            content = base64.b64decode(encoded, validate=True)
            portal.stage_attachment(metadata, content)
        except Exception as error:
            portal.mark_attachment_unavailable(attachment, error)

    def _request_idle_room_check_if_due(self) -> None:
        if self._room_portal is None or time.monotonic() < self._next_idle_room_check_at:
            return
        with self._worker_lock:
            worker = self._worker
        if worker is not None and worker.is_alive():
            return
        self._next_idle_room_check_at = time.monotonic() + self._idle_room_check_seconds
        try:
            self._command("room.check", {})
        except (BridgeReportRejected, BridgeReportTimeout) as error:
            print(
                f"Agent Bridge idle room check failed: {error.code}",
                file=sys.stderr,
                flush=True,
            )

    def _report_observation_receipt(self, through_seq: int) -> None:
        target_seq = max(0, int(through_seq))
        if target_seq <= self._last_observed_seq_reported:
            return
        ack = self._command("room.observed", {"through_seq": target_seq}) or {}
        result = ack.get("result") if isinstance(ack.get("result"), dict) else {}
        acknowledged_seq = max(0, int(result.get("observed_through_seq") or 0))
        if acknowledged_seq < target_seq:
            raise BridgeProtocolError(
                "room.observed ACK did not cover the provider's RoomPortal read receipt.",
                code="observed_receipt_incomplete",
                fatal=True,
            )
        self._last_observed_seq_reported = max(
            self._last_observed_seq_reported,
            acknowledged_seq,
        )

    def _fail_protocol(self, error: BridgeProtocolError) -> None:
        print(f"Agent Bridge protocol error: {error.code}", file=sys.stderr, flush=True)
        if not error.fatal:
            return
        self.reconnect_permitted = False
        self._stop.set()
        self.client.close()

    def _stop_after_report_failure(self, error: Exception, context: str) -> None:
        self.report_timeout_reconnect_requested = report_failure_allows_reconnect(error, context=context)
        self.reconnect_permitted = self.report_timeout_reconnect_requested
        self._stop.set()

    def _report_turn_failure(self, turn_id: str, error: Exception, *, context: str) -> None:
        if self._stop.is_set():
            return
        try:
            self._command(
                "turn.failed",
                turn_failure_payload(turn_id, error, self._failure_diagnostics()),
            )
        except (BridgeReportRejected, BridgeReportTimeout) as report_error:
            self._stop_after_report_failure(report_error, context)

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
            "bridge_launch_id": self.bridge_launch_id,
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
            # Counting denials without naming them made a permission bug
            # impossible to diagnose from the session record.
            "denied_permission_names": [
                str(name)[:128]
                for name in list(details.get("denied_permission_names") or [])[-5:]
            ],
            "notification_drop_count": int(details.get("notification_drop_count") or 0),
            "adapter_activity_invalid_count": activity_invalid_count,
            "message_source": str(details.get("message_source") or ""),
            "message_source_strict": bool(details.get("message_source_strict", False)),
            "model": str(details.get("model") or ""),
            "reasoning_effort": str(details.get("reasoning_effort") or ""),
            "service_tier": str(details.get("service_tier") or ""),
            "variant": str(details.get("variant") or ""),
            "execution_harness": str(
                details.get("execution_harness") or "builtin"
            ),
            "permission_mode": str(details.get("permission_mode") or ""),
            "runtime_kind": runtime_kind,
        }
        if self._runtime_profile is not None:
            payload.update(self._runtime_profile.report_fields())
        return payload


_ACTIVITY_CATEGORIES = frozenset(
    {"reasoning", "compaction", "file_read", "search", "command", "web", "tool"}
)


def _safe_activity(activity: object) -> dict[str, str]:
    values = activity if isinstance(activity, dict) else {}
    category = clean_lobby_text(values.get("category"), limit=32)
    status = clean_lobby_text(values.get("status"), limit=32)
    if category not in _ACTIVITY_CATEGORIES or status not in PUBLIC_ACTIVITY_STATUSES:
        return {}
    activity_id = safe_activity_id(values.get("activity_id"))
    activity_title = safe_activity_detail(values.get("activity_title"), limit=160)
    activity_detail = safe_activity_display_detail(
        values.get("activity_detail"),
        limit=2000 if category == "reasoning" else 600,
    )
    content, activity_kind = public_activity(
        category,
        status,
        detail=activity_detail or values.get("content"),
    )
    safe = {
        "activity_kind": activity_kind,
        "category": category,
        "status": status,
        "content": content,
    }
    if activity_id:
        safe["activity_id"] = activity_id
    if activity_title:
        safe["activity_title"] = activity_title
    if activity_detail:
        safe["activity_detail"] = activity_detail
    return safe


def _runtime_still_running(runtime: BridgeRuntime) -> bool:
    try:
        return bool(runtime.health().get("running", True))
    except Exception:
        return True


def _room_message_text(value: object, *, limit: int) -> str:
    return str(value or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")[:limit].strip()


def _contains_final_room_message(message: dict[str, object]) -> bool:
    if clean_lobby_text(message.get("stream"), limit=32) != "room_events":
        return False
    events = message.get("events") if isinstance(message.get("events"), list) else []
    return any(
        isinstance(event, dict)
        and clean_lobby_text(event.get("type"), limit=64) == "message_final"
        for event in events
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()
