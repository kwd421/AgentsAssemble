from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

from agentsassemble.agent_sessions import DEFAULT_ROOM_TURN_MAX_RECENT_EVENTS
from agentsassemble.gui import _make_handler
from agentsassemble.live_cli_smoke import _marker_recalled
from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.public_invite_runtime import PublicInviteRuntime
from agentsassemble.providers.bridge_process import NativeCliBridgeProcessManager
from agentsassemble.providers.launch_specs import (
    NativeCliProviderSpec,
    native_cli_provider_spec_from_config,
)
from agentsassemble.room.realtime import RoomRealtimeController
from agentsassemble.admission.invite_service import (
    SESSION_TOKEN_PREFIX,
    SESSION_TOKEN_TTL_SECONDS,
    InviteApplicationService,
)
from agentsassemble.persistence.local.admission.repository import (
    MemoryInviteSessionRepository,
)
from agentsassemble.admission.session_service import RoomSessionService
from agentsassemble.ws_room_client import WsRoomClient, connect_room_ws_with_ticket


STRICT_MESSAGE_SOURCES = {
    "codex": "codex_session_jsonl",
    "grok": "grok_acp",
    "antigravity": "antigravity_transcript_jsonl",
    "claude": "claude_session_jsonl",
}
TUI_NOISE = re.compile(
    r"(?:\x1b\[|Do you trust|Working\.\.\.|Thinking\.\.\.|ctrl\+|tokens?\b|permission mode|esc to|press enter)",
    re.IGNORECASE,
)
NON_ROOM_REPLY = re.compile(
    r"(?:<tool_call>|<tool_name>|AskUserQuestion|Plan mode is currently active|"
    r"I can only read files|What would you like me to do\?)",
    re.IGNORECASE,
)
ROOM_TTFO_P50_EXTRA_LIMIT_MS = 300.0
ROOM_TTFO_P95_EXTRA_LIMIT_MS = 750.0
ROOM_TTFO_P50_RATIO_LIMIT = 1.15
DEFAULT_CONVERSATION_TOPIC = (
    "자정 이후 폐쇄된 지하철역에서 안내방송이 아직 일어나지 않은 승객의 행동을 예고한다면, "
    "세 에이전트는 어떤 규칙으로 원인을 조사하고 서로를 믿을까?"
)


def run_room_native_cli_smoke(
    *,
    config_path: str | Path,
    output_root: str | Path = ".agentsassemble",
    providers: list[str] | None = None,
    approve_real_provider: bool = False,
    timeout_seconds: float = 180.0,
    latency_samples: int = 0,
    agent_conversation: bool = False,
    conversation_seconds: float = 0.0,
    conversation_topic: str = "",
    verify_controls: bool = False,
    observe_gui_port: int = 0,
) -> dict[str, object]:
    selected = [clean_lobby_text(value, limit=128) for value in list(providers or []) if value]
    run_id = "native_cli_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:6]
    result: dict[str, object] = {
        "run_id": run_id,
        "smoke": "room-native-cli",
        "room_id": "general",
        "transport": "/ws",
        "approved": bool(approve_real_provider),
        "requires_approval": True,
        "started_at": _now(),
        "latency_samples": max(0, int(latency_samples)),
        "mode": "agent_conversation" if agent_conversation else "provider_session",
        "conversation_seconds": max(0.0, float(conversation_seconds)),
        "conversation_topic": clean_lobby_text(conversation_topic, limit=2000),
        "verify_controls": bool(verify_controls),
        "observe_gui_port": max(0, int(observe_gui_port)),
        "observer_url": "",
        "provider_workspace_isolated": True,
        "latency_method": {
            "provider_direct": "same turn: provider runtime send started to first clean structured/transcript delta",
            "room_observed": "same turn: browser WebSocket message command sent to first room delta received",
            "warmup_turns": 1,
        },
        "providers": [],
    }
    if not approve_real_provider:
        result["status"] = "skipped"
        result["finished_at"] = _now()
        result["result_path"] = str(_write_result(output_root, result))
        return result

    specs = _load_specs(Path(config_path), selected, timeout_seconds=max(1.0, float(timeout_seconds)))
    with ExitStack() as stack:
        provider_workspace = Path(
            stack.enter_context(tempfile.TemporaryDirectory(prefix="agentsassemble-provider-workspace-"))
        )
        specs = [replace(spec, cwd=str(provider_workspace)) for spec in specs]
        if observe_gui_port > 0:
            server_root = Path(output_root).expanduser().resolve()
            server_root.mkdir(parents=True, exist_ok=True)
        else:
            temp_dir = stack.enter_context(
                tempfile.TemporaryDirectory(prefix="agentsassemble-native-smoke-")
            )
            server_root = Path(temp_dir) / "state"
        invite_repository = MemoryInviteSessionRepository()
        public_invite_runtime = PublicInviteRuntime(environ={})
        invite_application = InviteApplicationService(
            invite_repository,
            public_url=public_invite_runtime.public_url,
        )
        room_sessions = RoomSessionService(
            invite_repository,
            token_prefix=SESSION_TOKEN_PREFIX,
            ttl_seconds=SESSION_TOKEN_TTL_SECONDS,
            token_key=invite_application.signing_secret,
        )
        manager = NativeCliBridgeProcessManager(server_root)
        controller = RoomRealtimeController(
            server_root,
            invite_application=invite_application,
            room_sessions=room_sessions,
            providers=specs,
            bridge_manager=manager,
        )
        manager.set_exit_listener(controller.bridge_process_exited)
        server = ThreadingHTTPServer(
            ("127.0.0.1", max(0, int(observe_gui_port))),
            _make_handler(
                server_root,
                room_realtime_controller_override=controller,
                invite_repository_override=invite_repository,
                public_invite_runtime_override=public_invite_runtime,
            ),
        )
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        host, port = server.server_address
        base = f"http://{host}:{port}"
        if observe_gui_port > 0:
            result["observer_url"] = base + "/"
        client: WsRoomClient | None = None
        inbox: list[dict[str, object]] = []
        try:
            ticket = _host_ticket(base)
            client = connect_room_ws_with_ticket(base, ticket, ["room_events"], timeout=5.0)
            client.sock.settimeout(0.2)
            _wait_message(client, inbox, lambda item: item.get("op") == "snapshot", timeout_seconds=5.0)
            if agent_conversation:
                conversation = _smoke_agent_conversation(
                    client,
                    inbox,
                    controller,
                    manager,
                    specs,
                    timeout_seconds=max(1.0, float(timeout_seconds)),
                    conversation_seconds=max(0.0, float(conversation_seconds)),
                    conversation_topic=clean_lobby_text(conversation_topic, limit=2000),
                    verify_controls=bool(verify_controls),
                )
                result["conversation"] = conversation
                result["providers"] = list(conversation.get("providers") or [])
            else:
                for index in range(8):
                    controller.store.append_event(
                        "general",
                        "message_final",
                        participant_id="smoke-context",
                        participant_type="human",
                        actor_id="smoke-context",
                        actor_type="human",
                        display_name="Smoke Context",
                        content=f"bounded room history probe {index}",
                    )
                for spec in specs:
                    provider_result = _smoke_provider(
                        client,
                        inbox,
                        controller,
                        manager,
                        spec,
                        timeout_seconds=max(1.0, float(timeout_seconds)),
                        latency_samples=max(0, int(latency_samples)),
                    )
                    result["providers"].append(provider_result)  # type: ignore[index]
        finally:
            if client is not None:
                client.close()
            controller.close()
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=3.0)

    provider_results = [item for item in result["providers"] if isinstance(item, dict)]  # type: ignore[index]
    if agent_conversation:
        conversation = result.get("conversation") if isinstance(result.get("conversation"), dict) else {}
        result["status"] = conversation.get("status") or "error"
        result["metrics"] = conversation.get("metrics") if isinstance(conversation.get("metrics"), dict) else {}
    else:
        result["status"] = _overall_status(provider_results)
        result["metrics"] = _aggregate_metrics(provider_results)
    result["finished_at"] = _now()
    result["result_path"] = str(_write_result(output_root, result))
    return result


def _smoke_provider(
    client: WsRoomClient,
    inbox: list[dict[str, object]],
    controller: RoomRealtimeController,
    manager: NativeCliBridgeProcessManager,
    spec: NativeCliProviderSpec,
    *,
    timeout_seconds: float,
    latency_samples: int,
) -> dict[str, object]:
    resolved = _resolve_executable(spec.command)
    marker = f"{spec.agent_id}-{uuid4().hex[:8]}"
    result: dict[str, object] = {
        "agent_id": spec.agent_id,
        "display_name": spec.display_name,
        "provider_kind": spec.normalized_provider_kind(),
        "model": spec.model,
        "runtime_profile_key": spec.runtime_profile_key(),
        "command": list(spec.command),
        "resolved_executable": resolved,
        "transport": "pty+websocket",
        "pty": True,
        "is_one_shot": False,
        "status": "pending",
        "marker": marker,
        "memory_marker_recalled": False,
        "same_pid_over_turns": False,
        "bridge_pid": None,
        "provider_pids": [],
        "message_sources": [],
        "provider_visible_chars": [],
        "ttfo_ms": [],
        "turn_completed_ms": [],
        "provider_direct_ttfo_ms": [],
        "room_observed_ttfo_ms": [],
        "room_ttfo_extra_ms": [],
        "provider_direct_total_ms": [],
        "room_observed_total_ms": [],
        "room_total_extra_ms": [],
        "latency_sample_outputs": [],
        "latency_tui_noise_detected": False,
        "latency_phase": "not_started",
        "latency_acceptance": {"enforced": False, "passed": None, "reason": "not_requested"},
        "timeout_count": 0,
        "context_error_detected": False,
        "stderr_byte_count": 0,
        "stderr_line_count": 0,
        "stderr_warning_count": 0,
        "stderr_tail": "",
        "terminal_byte_count": 0,
        "terminal_tail": "",
        "rss_kb_delta": None,
        "alive_after_stop": None,
        "last_error": "",
        "output_tail": "",
        "outputs": [],
    }
    if not resolved:
        result.update(status="unavailable", last_error="configured command missing")
        return result

    launch: dict[str, object] = {}
    provider_pid = 0
    bridge_pid = 0
    stderr_path = Path()
    rss_start = 0
    try:
        start_request = client.command("agent.start", {"agent_id": spec.agent_id}, request_id=f"smoke-start-{spec.agent_id}-{uuid4().hex[:6]}")
        start_ack = _wait_ack(client, inbox, start_request, timeout_seconds=10.0)
        launch = dict(start_ack.get("result", {}).get("launch", {})) if isinstance(start_ack.get("result"), dict) else {}
        manager_health = controller.provider_process_health("general", spec.agent_id)
        bridge_pid = int(manager_health.get("bridge_pid") or 0)
        result["bridge_pid"] = bridge_pid or None
        session = _wait_session(controller, spec.agent_id, {"idle", "error"}, timeout_seconds=30.0)
        if session.get("runtime_status") != "idle":
            raise RuntimeError(str(session.get("last_error") or "provider did not become idle"))
        provider_pid = int(session.get("reported_provider_pid") or 0)
        result["transport"] = f"{session.get('transport') or 'unknown'}+websocket"
        result["pty"] = bool(session.get("pty", False))
        result["model"] = str(session.get("model") or result["model"])
        result["provider_pids"].append(provider_pid)  # type: ignore[union-attr]
        rss_start = _rss_kb(provider_pid)

        first = _run_turn(
            client,
            inbox,
            controller,
            spec.agent_id,
            f"@{spec.agent_id} AGENTSASSEMBLE_SESSION_MARKER={marker} 를 기억해. 기억했다고 한 문장으로만 답해.",
            previous_turn_count=0,
            timeout_seconds=timeout_seconds,
        )
        first_pid = int(first["session"].get("reported_provider_pid") or 0)  # type: ignore[union-attr]
        second = _run_turn(
            client,
            inbox,
            controller,
            spec.agent_id,
            f"@{spec.agent_id} 방금 기억하라고 한 AGENTSASSEMBLE_SESSION_MARKER 값을 정확히 말해.",
            previous_turn_count=1,
            timeout_seconds=timeout_seconds,
        )
        second_pid = int(second["session"].get("reported_provider_pid") or 0)  # type: ignore[union-attr]
        result["provider_pids"] = [first_pid, second_pid]
        result["same_pid_over_turns"] = bool(first_pid and first_pid == second_pid)
        result["memory_marker_recalled"] = _marker_recalled(marker, str(second["event"].get("content") or ""))  # type: ignore[union-attr]
        result["message_sources"] = [first["event"].get("message_source"), second["event"].get("message_source")]  # type: ignore[union-attr]
        result["provider_visible_chars"] = [first["provider_visible_chars"], second["provider_visible_chars"]]
        result["ttfo_ms"] = [first["ttfo_ms"], second["ttfo_ms"]]
        result["turn_completed_ms"] = [first["total_turn_ms"], second["total_turn_ms"]]
        result["output_tail"] = str(second["event"].get("content") or "")[-2000:]  # type: ignore[union-attr]
        result["outputs"] = [
            str(first["event"].get("content") or "")[-2000:],  # type: ignore[union-attr]
            str(second["event"].get("content") or "")[-2000:],  # type: ignore[union-attr]
        ]
        result["rss_kb_delta"] = _rss_kb(second_pid) - rss_start if rss_start else None
        result["stderr_byte_count"] = int(second["session"].get("stderr_byte_count") or 0)  # type: ignore[union-attr]
        result["stderr_line_count"] = int(second["session"].get("stderr_line_count") or 0)  # type: ignore[union-attr]
        result["stderr_warning_count"] = int(second["session"].get("stderr_warning_count") or 0)  # type: ignore[union-attr]
        result["stderr_tail"] = str(second["session"].get("stderr_tail") or "")[-16000:]  # type: ignore[union-attr]
        expected_source = STRICT_MESSAGE_SOURCES.get(spec.agent_id)
        if not result["same_pid_over_turns"]:
            raise RuntimeError("provider CLI PID changed between turns")
        if not result["memory_marker_recalled"]:
            raise RuntimeError("provider CLI did not recall the session marker")
        if expected_source and any(source != expected_source for source in result["message_sources"]):  # type: ignore[union-attr]
            raise RuntimeError(f"provider message source was not strict {expected_source}")
        if any(TUI_NOISE.search(str(turn["event"].get("content") or "")) for turn in (first, second)):  # type: ignore[union-attr]
            raise RuntimeError("provider message contained terminal UI chrome")

        if latency_samples:
            result["latency_phase"] = "warmup"
            warmup = _run_turn(
                client,
                inbox,
                controller,
                spec.agent_id,
                f"@{spec.agent_id} 지연 측정 준비야. WARMUP이라고만 답해.",
                previous_turn_count=2,
                timeout_seconds=timeout_seconds,
            )
            if int(warmup["session"].get("reported_provider_pid") or 0) != second_pid:  # type: ignore[union-attr]
                raise RuntimeError("provider CLI PID changed during latency warmup")
            latency_turns: list[dict[str, object]] = []
            for sample_index in range(latency_samples):
                result["latency_phase"] = f"sample_{sample_index + 1}_of_{latency_samples}"
                expected_output = f"LATENCY-{sample_index + 1}"
                turn = _run_turn(
                    client,
                    inbox,
                    controller,
                    spec.agent_id,
                    f"@{spec.agent_id} 지연 측정 {sample_index + 1}. LATENCY-{sample_index + 1}이라고만 답해.",
                    previous_turn_count=3 + sample_index,
                    timeout_seconds=timeout_seconds,
                )
                if int(turn["session"].get("reported_provider_pid") or 0) != second_pid:  # type: ignore[union-attr]
                    raise RuntimeError("provider CLI PID changed during latency samples")
                sample_output = str(turn["event"].get("content") or "")  # type: ignore[union-attr]
                result["latency_sample_outputs"].append(sample_output[-500:])  # type: ignore[union-attr]
                latency_turns.append(turn)
                _record_latency_comparison(result, latency_turns, enforce=bool(expected_source))
                if expected_source and sample_output != expected_output:
                    raise RuntimeError(
                        f"latency sample was not exactly {expected_output} "
                        f"(chars={len(sample_output)}): {sample_output[-500:]}"
                    )
                if TUI_NOISE.search(sample_output):
                    result["latency_tui_noise_detected"] = True
            result["latency_phase"] = "complete"
            acceptance = result["latency_acceptance"]
            if expected_source and result["latency_tui_noise_detected"]:
                raise RuntimeError("latency sample contained terminal UI chrome")
            if isinstance(acceptance, dict) and acceptance.get("enforced") and not acceptance.get("passed"):
                raise RuntimeError(
                    "room latency exceeded the direct CLI comparison threshold: "
                    + json.dumps(acceptance, ensure_ascii=False, sort_keys=True)
                )
        result["status"] = "ok"
    except TimeoutError as error:
        result["timeout_count"] = int(result["timeout_count"]) + 1
        result["status"] = "error"
        result["last_error"] = str(error)
    except Exception as error:
        result["status"] = "error"
        result["last_error"] = str(error)
    finally:
        try:
            stop_request = client.command("agent.stop", {"agent_id": spec.agent_id}, request_id=f"smoke-stop-{spec.agent_id}-{uuid4().hex[:6]}")
            _wait_ack(client, inbox, stop_request, timeout_seconds=8.0)
        except Exception as stop_error:
            result["last_error"] = str(result.get("last_error") or stop_error)
        _wait_until(lambda: not _pid_alive(provider_pid) and not _pid_alive(bridge_pid), timeout_seconds=5.0)
        provider_process_shared = spec.normalized_provider_kind() == "opencode_server"
        result["shared_provider_alive_after_agent_stop"] = provider_process_shared and _pid_alive(provider_pid)
        result["alive_after_stop"] = (not provider_process_shared and _pid_alive(provider_pid)) or _pid_alive(bridge_pid) or bool(
            manager.health("general", spec.agent_id).get("running")
        )
        bridge_stderr = _stderr_diagnostics(stderr_path)
        result["stderr_byte_count"] = int(result.get("stderr_byte_count") or 0) + int(
            bridge_stderr.get("stderr_byte_count") or 0
        )
        result["stderr_line_count"] = int(result.get("stderr_line_count") or 0) + int(
            bridge_stderr.get("stderr_line_count") or 0
        )
        result["stderr_warning_count"] = int(result.get("stderr_warning_count") or 0) + int(
            bridge_stderr.get("stderr_warning_count") or 0
        )
        result["stderr_tail"] = "\n".join(
            part
            for part in (str(result.get("stderr_tail") or ""), str(bridge_stderr.get("stderr_tail") or ""))
            if part
        )[-16000:]
        errors = [
            event
            for event in controller.store.read_events("general")
            if event.get("participant_id") == spec.agent_id and event.get("type") == "error"
        ]
        if errors:
            diagnostics = errors[-1].get("diagnostics") if isinstance(errors[-1].get("diagnostics"), dict) else {}
            result["terminal_byte_count"] = int(diagnostics.get("terminal_byte_count") or 0)
            result["terminal_tail"] = str(diagnostics.get("terminal_tail") or "")[-16000:]
            result["stderr_byte_count"] = max(
                int(result.get("stderr_byte_count") or 0), int(diagnostics.get("stderr_byte_count") or 0)
            )
            result["stderr_line_count"] = max(
                int(result.get("stderr_line_count") or 0), int(diagnostics.get("stderr_line_count") or 0)
            )
            result["stderr_warning_count"] = max(
                int(result.get("stderr_warning_count") or 0), int(diagnostics.get("stderr_warning_count") or 0)
            )
            result["stderr_tail"] = str(diagnostics.get("stderr_tail") or result.get("stderr_tail") or "")[-16000:]
        result["context_error_detected"] = any("context" in str(event.get("content") or "").casefold() for event in errors)
        if result["alive_after_stop"] and result["status"] == "ok":
            result["status"] = "error"
            result["last_error"] = "provider or bridge process remained alive after stop"
    values = [float(value) for value in result["ttfo_ms"] if isinstance(value, (int, float))]  # type: ignore[union-attr]
    totals = [float(value) for value in result["turn_completed_ms"] if isinstance(value, (int, float))]  # type: ignore[union-attr]
    result["p50_time_to_first_agent_delta_ms"] = _percentile(values, 50)
    result["p95_time_to_first_agent_delta_ms"] = _percentile(values, 95)
    result["p50_turn_completed_ms"] = _percentile(totals, 50)
    result["p95_turn_completed_ms"] = _percentile(totals, 95)
    return result


def _smoke_agent_conversation(
    client: WsRoomClient,
    inbox: list[dict[str, object]],
    controller: RoomRealtimeController,
    manager: NativeCliBridgeProcessManager,
    specs: list[NativeCliProviderSpec],
    *,
    timeout_seconds: float,
    conversation_seconds: float = 0.0,
    conversation_topic: str = "",
    verify_controls: bool = False,
) -> dict[str, object]:
    requested_seconds = max(0.0, float(conversation_seconds))
    topic = clean_lobby_text(conversation_topic, limit=2000) or DEFAULT_CONVERSATION_TOPIC
    result: dict[str, object] = {
        "status": "pending",
        "topology": "server_assigned_shared_room",
        "topic": topic,
        "requested_duration_seconds": requested_seconds,
        "actual_duration_seconds": 0.0,
        "speaker_cycles_completed": 0,
        "timebox_met": requested_seconds == 0.0,
        "control_checks": [],
        "providers": [],
        "turns": [],
        "speaker_order": [spec.agent_id for spec in specs],
        "topic_event_id": "",
        "visible_at_mention_count": 0,
        "all_agents_saw_full_peer_context_after_warmup": None,
        "unexpected_extra_turns": False,
        "last_error": "",
    }
    if len(specs) < 2:
        result.update(status="error", last_error="Agent conversation smoke requires at least two providers.")
        return result

    provider_results: dict[str, dict[str, object]] = {}
    started: list[NativeCliProviderSpec] = []
    try:
        for spec in specs:
            resolved = _resolve_executable(spec.command)
            provider_result: dict[str, object] = {
                "agent_id": spec.agent_id,
                "display_name": spec.display_name,
                "provider_kind": spec.normalized_provider_kind(),
                "configured_model": spec.model,
                "configured_command": list(spec.command),
                "runtime_profile_key": spec.runtime_profile_key(),
                "resolved_executable": resolved,
                "bridge_pid": None,
                "provider_pids": [],
                "same_pid_over_turns": False,
                "turn_count": 0,
                "alive_after_stop": None,
                "status": "pending",
            }
            provider_results[spec.agent_id] = provider_result
            result["providers"].append(provider_result)  # type: ignore[union-attr]
            if not resolved:
                raise RuntimeError(f"{spec.agent_id}: configured command missing")
            request_id = client.command(
                "agent.start",
                {"agent_id": spec.agent_id},
                request_id=f"conversation-start-{spec.agent_id}-{uuid4().hex[:6]}",
            )
            ack = _wait_ack(client, inbox, request_id, timeout_seconds=10.0)
            launch = dict(ack.get("result", {}).get("launch", {})) if isinstance(ack.get("result"), dict) else {}
            started.append(spec)
            session = _wait_session(controller, spec.agent_id, {"idle", "error"}, timeout_seconds=30.0)
            if session.get("runtime_status") != "idle":
                raise RuntimeError(f"{spec.agent_id}: {session.get('last_error') or 'provider did not become idle'}")
            manager_health = controller.provider_process_health("general", spec.agent_id)
            provider_result["bridge_pid"] = int(manager_health.get("bridge_pid") or 0) or None
            provider_pid = int(session.get("reported_provider_pid") or 0)
            if provider_pid <= 0:
                raise RuntimeError(f"{spec.agent_id}: provider PID was not reported")
            provider_result["provider_pids"] = [provider_pid]
            provider_result["transport"] = session.get("transport") or "unknown"
            provider_result["pty"] = bool(session.get("pty", False))
            provider_result["reported_model"] = session.get("model") or spec.model

        conversation_started = time.monotonic()
        topic_before_seq = controller.store.latest_event_sequence("general")
        topic_sent_at = time.monotonic()
        topic_request = client.command(
            "message.send",
            {
                "content": (
                    f"이 방의 에이전트들이 함께 이야기할 주제: {topic}\n"
                    "지금까지 이 공개 방에서 오간 발언을 읽고 직전 의견을 자연스럽게 이어가. "
                    "서버가 발언 순서를 정하므로 다른 참가자를 호출하거나 테스트 표식을 쓰지 말고, "
                    "한 번에 2~4문장으로 의견이나 질문을 남겨."
                ),
                "target_agent_id": specs[0].agent_id,
            },
            request_id=f"group-topic-{uuid4().hex[:6]}",
        )
        topic_ack = _wait_ack(client, inbox, topic_request, timeout_seconds=8.0)
        topic_result = topic_ack.get("result") if isinstance(topic_ack.get("result"), dict) else {}
        topic_event = topic_result.get("event") if isinstance(topic_result.get("event"), dict) else {}
        if not topic_event.get("id"):
            raise RuntimeError("Shared-room topic message was not appended.")
        result["topic_event_id"] = topic_event["id"]
        previous_event = dict(topic_event)
        all_agent_ids = {item.agent_id for item in specs}
        turn_index = 0
        cycle_index = 0
        while True:
            cycle_index += 1
            for spec in specs:
                turn_index += 1
                already_assigned = turn_index == 1
                turn_result = _run_group_speaker_turn(
                    client,
                    inbox,
                    controller,
                    spec=spec,
                    source_event=previous_event,
                    timeout_seconds=timeout_seconds,
                    cycle_index=cycle_index,
                    sequence_index=turn_index,
                    all_agent_ids=all_agent_ids,
                    already_assigned=already_assigned,
                    observed_from=topic_sent_at if already_assigned else None,
                    after_seq=topic_before_seq if already_assigned else None,
                )
                result["turns"].append(turn_result)  # type: ignore[union-attr]
                previous_event = controller.store.event_by_id(
                    "general",
                    clean_lobby_text(turn_result.get("message_event_id"), limit=128),
                )
                result["actual_duration_seconds"] = round(time.monotonic() - conversation_started, 3)
            elapsed = time.monotonic() - conversation_started
            result["speaker_cycles_completed"] = cycle_index
            if requested_seconds <= 0.0 or elapsed >= requested_seconds:
                break
        actual_duration = time.monotonic() - conversation_started
        result["actual_duration_seconds"] = round(actual_duration, 3)
        result["speaker_cycles_completed"] = cycle_index
        result["timebox_met"] = requested_seconds <= 0.0 or actual_duration >= requested_seconds
        conversation_turns = [item for item in list(result["turns"]) if isinstance(item, dict)]
        result["visible_at_mention_count"] = sum(
            str(item.get("output") or "").count("@") for item in conversation_turns
        ) + str(topic_event.get("content") or "").count("@")
        warm_turns = [item for item in conversation_turns if int(item.get("cycle_index") or 0) > 1]
        result["all_agents_saw_full_peer_context_after_warmup"] = (
            all(bool(item.get("full_peer_context_seen")) for item in warm_turns)
            if warm_turns
            else None
        )
        if result["visible_at_mention_count"]:
            raise RuntimeError("Shared-room conversation emitted a visible at-mention.")
        if warm_turns and not result["all_agents_saw_full_peer_context_after_warmup"]:
            raise RuntimeError("At least one Agent Session missed a peer's public room message.")

        if verify_controls:
            for spec in specs:
                control_result = _verify_pause_resume(
                    client,
                    inbox,
                    controller,
                    manager,
                    spec,
                    timeout_seconds=timeout_seconds,
                )
                result["control_checks"].append(control_result)  # type: ignore[union-attr]

        turn_counts_before_quiet = {
            spec.agent_id: int(controller.store.session("general", spec.agent_id).get("turn_count") or 0)
            for spec in specs
        }
        sessions_quiet_before = all(
            controller.store.session("general", spec.agent_id).get("runtime_status") == "idle"
            and not controller.store.session("general", spec.agent_id).get("pending_event_ids")
            for spec in specs
        )
        time.sleep(1.0)
        actual_turns = {
            spec.agent_id: int(controller.store.session("general", spec.agent_id).get("turn_count") or 0)
            for spec in specs
        }
        sessions_quiet_after = all(
            controller.store.session("general", spec.agent_id).get("runtime_status") == "idle"
            and not controller.store.session("general", spec.agent_id).get("pending_event_ids")
            for spec in specs
        )
        result["turn_counts_before_quiet_window"] = turn_counts_before_quiet
        result["actual_turn_counts"] = actual_turns
        result["sessions_quiet"] = sessions_quiet_before and sessions_quiet_after
        minimum_expected_turns = 1 + (1 if verify_controls else 0)
        result["unexpected_extra_turns"] = (
            actual_turns != turn_counts_before_quiet
            or not result["sessions_quiet"]
            or any(count < minimum_expected_turns for count in actual_turns.values())
        )
        if result["unexpected_extra_turns"]:
            raise RuntimeError(
                "Shared-room speaker queue did not settle: "
                f"before={turn_counts_before_quiet!r}, after={actual_turns!r}"
            )

        for spec in specs:
            session = controller.store.session("general", spec.agent_id)
            provider_result = provider_results[spec.agent_id]
            first_pid = int(list(provider_result["provider_pids"])[0])
            final_pid = int(session.get("reported_provider_pid") or 0)
            provider_result["provider_pids"] = [first_pid, final_pid]
            provider_result["same_pid_over_turns"] = bool(first_pid and first_pid == final_pid)
            provider_result["turn_count"] = int(session.get("turn_count") or 0)
            provider_result["stderr_byte_count"] = int(session.get("stderr_byte_count") or 0)
            provider_result["stderr_line_count"] = int(session.get("stderr_line_count") or 0)
            provider_result["stderr_warning_count"] = int(session.get("stderr_warning_count") or 0)
            provider_result["provider_visible_chars"] = int(session.get("provider_visible_chars") or 0)
            provider_result["provider_visible_event_count"] = int(session.get("provider_visible_event_count") or 0)
            provider_result["pause_resume_verified"] = bool(
                any(
                    item.get("agent_id") == spec.agent_id and all(dict(item.get("checks") or {}).values())
                    for item in list(result.get("control_checks") or [])
                    if isinstance(item, dict)
                )
            ) if verify_controls else None
            provider_result["status"] = "ok"
            if not provider_result["same_pid_over_turns"]:
                raise RuntimeError(f"{spec.agent_id}: provider PID changed during the conversation")
        result["metrics"] = _conversation_metrics(list(result["turns"]))  # type: ignore[arg-type]
        result["status"] = "ok"
    except TimeoutError as error:
        result["status"] = "error"
        result["last_error"] = str(error)
    except Exception as error:
        result["status"] = "error"
        result["last_error"] = str(error)
    finally:
        for spec in reversed(started):
            provider_result = provider_results[spec.agent_id]
            provider_pids = list(provider_result.get("provider_pids") or [])
            provider_pid = int(provider_pids[-1] or 0) if provider_pids else 0
            bridge_pid = int(provider_result.get("bridge_pid") or 0)
            cleanup_ack: dict[str, object] = {}
            restart_response: dict[str, object] = {}
            provider_result["cleanup_action"] = "participant.kick" if verify_controls else "agent.stop"
            try:
                if verify_controls:
                    request_id = client.command(
                        "participant.kick",
                        {"participant_id": spec.agent_id},
                        request_id=f"conversation-kick-{spec.agent_id}-{uuid4().hex[:6]}",
                    )
                    cleanup_ack = _wait_ack(client, inbox, request_id, timeout_seconds=8.0)
                    restart_request = client.command(
                        "agent.start",
                        {"agent_id": spec.agent_id},
                        request_id=f"conversation-kick-restart-{spec.agent_id}-{uuid4().hex[:6]}",
                    )
                    restart_response = _wait_message(
                        client,
                        inbox,
                        lambda item: item.get("request_id") == restart_request
                        and item.get("op") in {"ack", "nack"},
                        timeout_seconds=8.0,
                    )
                else:
                    request_id = client.command(
                        "agent.stop",
                        {"agent_id": spec.agent_id},
                        request_id=f"conversation-stop-{spec.agent_id}-{uuid4().hex[:6]}",
                    )
                    cleanup_ack = _wait_ack(client, inbox, request_id, timeout_seconds=8.0)
            except Exception as stop_error:
                if not result.get("last_error"):
                    result["last_error"] = str(stop_error)
                    result["status"] = "error"
            _wait_until(lambda: not _pid_alive(provider_pid) and not _pid_alive(bridge_pid), timeout_seconds=5.0)
            provider_process_shared = spec.normalized_provider_kind() == "opencode_server"
            provider_result["shared_provider_alive_after_agent_stop"] = provider_process_shared and _pid_alive(provider_pid)
            provider_result["alive_after_stop"] = (not provider_process_shared and _pid_alive(provider_pid)) or _pid_alive(bridge_pid) or bool(
                manager.health("general", spec.agent_id).get("running")
            )
            if verify_controls:
                cleanup_result = cleanup_ack.get("result") if isinstance(cleanup_ack.get("result"), dict) else {}
                participant = (
                    cleanup_result.get("participant")
                    if isinstance(cleanup_result.get("participant"), dict)
                    else {}
                )
                restart_error = (
                    restart_response.get("error")
                    if isinstance(restart_response.get("error"), dict)
                    else {}
                )
                kick_checks = {
                    "kick_acknowledged": cleanup_ack.get("op") == "ack",
                    "participant_marked_kicked": participant.get("status") == "kicked",
                    "provider_and_bridge_stopped": not provider_result["alive_after_stop"],
                    "restart_requires_explicit_re_add": bool(
                        restart_response.get("op") == "nack" and restart_error.get("code") == "not_found"
                    ),
                }
                provider_result["kick_checks"] = kick_checks
                provider_result["kick_verified"] = all(kick_checks.values())
                if not provider_result["kick_verified"]:
                    result["status"] = "error"
                    failed = sorted(name for name, passed in kick_checks.items() if not passed)
                    result["last_error"] = result.get("last_error") or (
                        f"{spec.agent_id}: kick checks failed: {', '.join(failed)}"
                    )
            if provider_result["alive_after_stop"]:
                provider_result["status"] = "error"
                result["status"] = "error"
                result["last_error"] = result.get("last_error") or f"{spec.agent_id}: process remained alive after stop"
        for spec in specs:
            provider_result = provider_results.get(spec.agent_id)
            if provider_result is not None and provider_result.get("status") == "pending":
                provider_result["status"] = "error"
    return result


def _verify_pause_resume(
    client: WsRoomClient,
    inbox: list[dict[str, object]],
    controller: RoomRealtimeController,
    manager: NativeCliBridgeProcessManager,
    spec: NativeCliProviderSpec,
    *,
    timeout_seconds: float,
) -> dict[str, object]:
    before = controller.store.session("general", spec.agent_id)
    before_pid = int(before.get("reported_provider_pid") or 0)
    before_bridge_pid = int(before.get("bridge_pid") or 0)
    before_turn_count = int(before.get("turn_count") or 0)
    before_seq = controller.store.latest_event_sequence("general")
    if before.get("runtime_status") != "idle" or before.get("pending_event_ids"):
        raise RuntimeError(f"{spec.agent_id}: session was not quiet before pause verification")

    pause_request = client.command(
        "agent.pause",
        {"agent_id": spec.agent_id},
        request_id=f"conversation-pause-{spec.agent_id}-{uuid4().hex[:6]}",
    )
    pause_ack = _wait_ack(client, inbox, pause_request, timeout_seconds=8.0)
    paused = controller.store.session("general", spec.agent_id)
    marker = f"PAUSE-RESUME-{spec.agent_id}-{uuid4().hex[:6]}".upper()
    queued_request = client.command(
        "message.send",
        {
            "content": (
                f"일시정지 backlog 전달 확인 코드 {marker}야. "
                f"답변을 {marker}로 시작하는 한 문장으로만 써."
            ),
            "target_agent_id": spec.agent_id,
        },
        request_id=f"conversation-paused-message-{spec.agent_id}-{uuid4().hex[:6]}",
    )
    queued_ack = _wait_ack(client, inbox, queued_request, timeout_seconds=8.0)
    queued_event = (
        queued_ack.get("result", {}).get("event", {})
        if isinstance(queued_ack.get("result"), dict)
        and isinstance(queued_ack.get("result", {}).get("event"), dict)
        else {}
    )
    queued_event_id = clean_lobby_text(queued_event.get("id"), limit=128)
    time.sleep(0.5)
    waiting = controller.store.session("general", spec.agent_id)

    resumed_at = time.monotonic()
    resume_request = client.command(
        "agent.resume",
        {"agent_id": spec.agent_id},
        request_id=f"conversation-resume-{spec.agent_id}-{uuid4().hex[:6]}",
    )
    resume_ack = _wait_ack(client, inbox, resume_request, timeout_seconds=8.0)
    observed = _wait_agent_final_event(
        client,
        inbox,
        spec.agent_id,
        after_seq=before_seq,
        observed_from=resumed_at,
        timeout_seconds=timeout_seconds,
    )
    final_event = observed["event"]
    final_session = _wait_until_value(
        lambda: controller.store.session("general", spec.agent_id),
        lambda session: int(session.get("turn_count") or 0) > before_turn_count
        and session.get("runtime_status") == "idle"
        and not session.get("pending_event_ids"),
        timeout_seconds=8.0,
    )
    resume_result = resume_ack.get("result") if isinstance(resume_ack.get("result"), dict) else {}
    output = str(final_event.get("content") or "")
    expected_source = STRICT_MESSAGE_SOURCES.get(spec.agent_id)
    checks = {
        "pause_acknowledged": bool(
            isinstance(pause_ack.get("result"), dict)
            and pause_ack.get("result", {}).get("process_preserved")
        ),
        "paused_without_process_exit": bool(
            paused.get("runtime_status") == "paused"
            and not paused.get("enabled")
            and int(paused.get("reported_provider_pid") or 0) == before_pid
            and int(paused.get("bridge_pid") or 0) == before_bridge_pid
            and _pid_alive(before_pid)
            and bool(manager.health("general", spec.agent_id).get("running"))
        ),
        "paused_message_not_dispatched": bool(
            waiting.get("runtime_status") == "paused"
            and int(waiting.get("turn_count") or 0) == before_turn_count
        ),
        "backlog_recorded": bool(queued_event_id and queued_event_id in list(waiting.get("pending_event_ids") or [])),
        "resume_reused_runtime": bool(resume_result.get("runtime_reused") and resume_result.get("process_reused")),
        "same_provider_pid_after_resume": int(final_session.get("reported_provider_pid") or 0) == before_pid,
        "same_bridge_pid_after_resume": int(final_session.get("bridge_pid") or 0) == before_bridge_pid,
        "backlog_event_was_turn_source": final_event.get("source_event_id") == queued_event_id,
        "resume_output_contains_marker": marker.casefold() in output.casefold(),
        "resume_output_clean": not bool(TUI_NOISE.search(output)),
        "resume_output_structured": not expected_source or final_event.get("message_source") == expected_source,
        "backlog_cleared": not final_session.get("pending_event_ids"),
    }
    if not all(checks.values()):
        failed = sorted(name for name, passed in checks.items() if not passed)
        raise RuntimeError(f"{spec.agent_id}: pause/resume checks failed: {', '.join(failed)}")
    return {
        "agent_id": spec.agent_id,
        "marker": marker,
        "pid_before": before_pid,
        "pid_after": int(final_session.get("reported_provider_pid") or 0),
        "bridge_pid_before": before_bridge_pid,
        "bridge_pid_after": int(final_session.get("bridge_pid") or 0),
        "queued_event_id": queued_event_id,
        "final_source_event_id": final_event.get("source_event_id"),
        "ttfo_ms": observed["ttfo_ms"],
        "output": output[-2000:],
        "checks": checks,
    }


def _run_group_speaker_turn(
    client: WsRoomClient,
    inbox: list[dict[str, object]],
    controller: RoomRealtimeController,
    *,
    spec: NativeCliProviderSpec,
    source_event: dict[str, object],
    timeout_seconds: float,
    cycle_index: int = 1,
    sequence_index: int = 1,
    all_agent_ids: set[str],
    already_assigned: bool = False,
    observed_from: float | None = None,
    after_seq: int | None = None,
) -> dict[str, object]:
    source_event_id = clean_lobby_text(source_event.get("id"), limit=128)
    source_event_seq = int(source_event.get("seq") or 0)
    if not source_event_id or source_event.get("type") != "message_final":
        raise RuntimeError(f"{spec.agent_id}: group turn source was not a public room message")
    session_before = controller.store.session("general", spec.agent_id)
    previous_turn_count = int(session_before.get("turn_count") or 0)
    clean_after_seq = (
        int(after_seq)
        if after_seq is not None
        else controller.store.latest_event_sequence("general")
    )
    started = observed_from if observed_from is not None else time.monotonic()
    if not already_assigned:
        assigned = controller.request_agent_turn(
            "general",
            spec.agent_id,
            source_event_id=source_event_id,
        )
        if not assigned.get("assigned"):
            raise RuntimeError(f"{spec.agent_id}: server floor did not assign the requested group turn")
    observed = _wait_agent_final_event(
        client,
        inbox,
        spec.agent_id,
        after_seq=clean_after_seq,
        observed_from=started,
        timeout_seconds=timeout_seconds,
    )
    completed_at = time.monotonic()
    event = observed["event"]
    session_after = _wait_until_value(
        lambda: controller.store.session("general", spec.agent_id),
        lambda session: int(session.get("turn_count") or 0) > previous_turn_count
        and session.get("runtime_status") == "idle"
        and not session.get("pending_event_ids"),
        timeout_seconds=8.0,
    )
    turn_started = next(
        (
            event
            for event in reversed(controller.store.read_events("general"))
            if event.get("type") == "turn_started"
            and event.get("participant_id") == spec.agent_id
            and event.get("turn_id") == observed["event"].get("turn_id")
        ),
        {},
    )
    context_after_seq = int(turn_started.get("provider_context_after_seq") or 0)
    context_up_to_seq = int(turn_started.get("provider_context_up_to_seq") or 0)
    expected_context = controller.store.read_events(
        "general",
        after_seq=context_after_seq,
        before_seq=context_up_to_seq + 1,
        limit=DEFAULT_ROOM_TURN_MAX_RECENT_EVENTS,
        newest=True,
        event_types=("message_final",),
        exclude_actor_id=spec.agent_id,
    )
    expected_context_ids = [clean_lobby_text(item.get("id"), limit=128) for item in expected_context]
    context_event_ids = [
        clean_lobby_text(value, limit=128)
        for value in list(turn_started.get("provider_context_event_ids") or [])
        if clean_lobby_text(value, limit=128)
    ]
    context_actor_ids = [
        clean_lobby_text(value, limit=128)
        for value in list(turn_started.get("provider_context_actor_ids") or [])
        if clean_lobby_text(value, limit=128)
    ]
    peer_actor_ids = sorted((set(context_actor_ids) & all_agent_ids) - {spec.agent_id})
    expected_peers = all_agent_ids - {spec.agent_id}
    full_peer_context_seen = expected_peers.issubset(set(context_actor_ids)) if cycle_index > 1 else None
    output = str(event.get("content") or "")
    expected_source = STRICT_MESSAGE_SOURCES.get(spec.agent_id)
    checks = {
        "server_turn_sourced_from_previous_public_message": turn_started.get("source_event_id") == source_event_id,
        "final_sourced_from_previous_public_message": event.get("source_event_id") == source_event_id,
        "previous_public_message_was_visible": source_event_id in context_event_ids,
        "entire_bounded_public_diff_was_visible": context_event_ids == expected_context_ids,
        "context_cursor_reached_source": context_up_to_seq == source_event_seq,
        "message_has_no_at_mention": "@" not in output,
        "message_clean": not bool(TUI_NOISE.search(output)),
        "message_is_room_reply": not bool(NON_ROOM_REPLY.search(output)),
        "message_structured": not expected_source or event.get("message_source") == expected_source,
    }
    if not all(checks.values()):
        failed = sorted(name for name, passed in checks.items() if not passed)
        raise RuntimeError(f"{spec.agent_id}: shared-room turn checks failed: {', '.join(failed)}")
    return {
        "sequence_index": sequence_index,
        "agent_turn_count": int(session_after.get("turn_count") or 0),
        "cycle_index": cycle_index,
        "agent_id": spec.agent_id,
        "source_event_id": source_event_id,
        "message_event_id": event.get("id"),
        "message_source": event.get("message_source"),
        "output": output[-4000:],
        "ttfo_ms": observed["ttfo_ms"],
        "turn_completed_ms": round((completed_at - started) * 1000, 1),
        "provider_visible_chars": turn_started.get("provider_visible_chars"),
        "provider_visible_event_count": turn_started.get("provider_visible_event_count"),
        "provider_context_event_ids": context_event_ids,
        "provider_context_actor_ids": context_actor_ids,
        "peer_actor_ids_seen": peer_actor_ids,
        "full_peer_context_seen": full_peer_context_seen,
        "last_seen_event_id": session_after.get("last_seen_event_id"),
        "checks": checks,
    }


def _wait_agent_final_event(
    client: WsRoomClient,
    inbox: list[dict[str, object]],
    agent_id: str,
    *,
    after_seq: int,
    observed_from: float,
    timeout_seconds: float,
) -> dict[str, object]:
    first_delta_at: float | None = None

    def matching_event(item: dict[str, object]) -> bool:
        nonlocal first_delta_at
        if item.get("op") != "event":
            return False
        matched_final = False
        for candidate in list(item.get("events") or []):
            if not isinstance(candidate, dict):
                continue
            if int(candidate.get("seq") or 0) <= after_seq or candidate.get("participant_id") != agent_id:
                continue
            if candidate.get("type") == "message_delta" and first_delta_at is None:
                first_delta_at = time.monotonic()
            if candidate.get("type") in {"message_final", "error"}:
                matched_final = True
        return matched_final

    pushed = _wait_message(client, inbox, matching_event, timeout_seconds=timeout_seconds + 10.0)
    event = next(
        event
        for event in list(pushed.get("events") or [])
        if isinstance(event, dict)
        and int(event.get("seq") or 0) > after_seq
        and event.get("participant_id") == agent_id
        and event.get("type") in {"message_final", "error"}
    )
    if event.get("type") == "error":
        detail = str(event.get("content") or "provider turn failed")
        raise RuntimeError(f"{agent_id}: {detail}")
    completed_at = time.monotonic()
    return {
        "event": event,
        "ttfo_ms": round(((first_delta_at or completed_at) - observed_from) * 1000, 1),
    }


def _run_turn(
    client: WsRoomClient,
    inbox: list[dict[str, object]],
    controller: RoomRealtimeController,
    agent_id: str,
    content: str,
    *,
    previous_turn_count: int,
    timeout_seconds: float,
) -> dict[str, object]:
    before_seq = controller.store.latest_event_sequence("general")
    started = time.monotonic()
    first_delta_at: float | None = None
    request_id = client.command(
        "message.send",
        {"content": content},
        request_id=f"smoke-message-{agent_id}-{uuid4().hex[:8]}",
    )
    _wait_ack(client, inbox, request_id, timeout_seconds=8.0)

    def matching_event(item: dict[str, object]) -> bool:
        nonlocal first_delta_at
        if item.get("op") != "event":
            return False
        matched_final = False
        for candidate in list(item.get("events") or []):
            if not isinstance(candidate, dict):
                continue
            if int(candidate.get("seq") or 0) <= before_seq or candidate.get("participant_id") != agent_id:
                continue
            if candidate.get("type") == "message_delta" and first_delta_at is None:
                first_delta_at = time.monotonic()
            if candidate.get("type") in {"message_final", "error"}:
                matched_final = True
        return matched_final

    pushed = _wait_message(client, inbox, matching_event, timeout_seconds=timeout_seconds + 10.0)
    event = next(
        event
        for event in list(pushed.get("events") or [])
        if isinstance(event, dict)
        and int(event.get("seq") or 0) > before_seq
        and event.get("type") in {"message_final", "error"}
        and event.get("participant_id") == agent_id
    )
    if event.get("type") == "error":
        raise RuntimeError(str(event.get("content") or "provider turn failed"))
    observed_completed_at = time.monotonic()
    session = _wait_until_value(
        lambda: controller.store.session("general", agent_id),
        lambda value: int(value.get("turn_count") or 0) > previous_turn_count and value.get("runtime_status") == "idle",
        timeout_seconds=5.0,
    )
    latency = dict(session.get("latency")) if isinstance(session.get("latency"), dict) else {}
    turn_started = next(
        (
            candidate
            for candidate in reversed(controller.store.read_events("general"))
            if candidate.get("type") == "turn_started"
            and candidate.get("participant_id") == agent_id
            and int(candidate.get("seq") or 0) > before_seq
        ),
        {},
    )
    provider_model_ttfo = _optional_float(latency.get("ttfo_ms"))
    input_write_ms = _optional_float(latency.get("input_write_ms"))
    provider_ttfo = (
        round(provider_model_ttfo + input_write_ms, 1)
        if provider_model_ttfo is not None and input_write_ms is not None
        else provider_model_ttfo
    )
    provider_total = _optional_float(latency.get("total_turn_ms"))
    room_ttfo = round(((first_delta_at or observed_completed_at) - started) * 1000, 1)
    room_total = round((observed_completed_at - started) * 1000, 1)
    return {
        "event": event,
        "session": session,
        "ttfo_ms": provider_model_ttfo,
        "total_turn_ms": provider_total or room_total,
        "provider_model_ttfo_ms": provider_model_ttfo,
        "provider_input_write_ms": input_write_ms,
        "provider_direct_ttfo_ms": provider_ttfo,
        "room_observed_ttfo_ms": room_ttfo,
        "room_ttfo_extra_ms": round(room_ttfo - provider_ttfo, 1) if provider_ttfo is not None else None,
        "provider_direct_total_ms": provider_total,
        "room_observed_total_ms": room_total,
        "room_total_extra_ms": round(room_total - provider_total, 1) if provider_total is not None else None,
        "provider_visible_chars": turn_started.get("provider_visible_chars"),
    }


def _load_specs(config_path: Path, selected: list[str], *, timeout_seconds: float) -> list[NativeCliProviderSpec]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("providers"), list):
        raise ValueError("live CLI smoke config must contain a providers array")
    selected_set = {value.casefold() for value in selected}
    specs: list[NativeCliProviderSpec] = []
    for item in payload["providers"]:
        if not isinstance(item, dict):
            continue
        agent_id = clean_lobby_text(item.get("id"), limit=128)
        if selected_set and agent_id.casefold() not in selected_set:
            continue
        specs.append(
            native_cli_provider_spec_from_config(
                item,
                turn_timeout_seconds=timeout_seconds,
            )
        )
    if selected_set:
        found = {spec.agent_id.casefold() for spec in specs}
        missing = sorted(selected_set - found)
        if missing:
            raise ValueError(f"Provider ids are missing from smoke config: {', '.join(missing)}")
    return specs


def _host_ticket(base: str) -> str:
    request = Request(
        f"{base}/api/ws-ticket",
        data=json.dumps({"meeting_id": "general"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5.0) as response:
        return str(json.loads(response.read().decode("utf-8"))["ticket"])


def _wait_ack(client: WsRoomClient, inbox: list[dict[str, object]], request_id: str, *, timeout_seconds: float) -> dict[str, object]:
    message = _wait_message(
        client,
        inbox,
        lambda item: item.get("request_id") == request_id and item.get("op") in {"ack", "nack"},
        timeout_seconds=timeout_seconds,
    )
    if message.get("op") == "nack":
        error = message.get("error") if isinstance(message.get("error"), dict) else {}
        raise RuntimeError(str(error.get("message") or "room command rejected"))
    return message


def _wait_message(
    client: WsRoomClient,
    inbox: list[dict[str, object]],
    predicate,
    *,
    timeout_seconds: float,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for index, message in enumerate(inbox):
            if predicate(message):
                return inbox.pop(index)
        received = client.receive()
        for index, message in enumerate(received):
            if predicate(message):
                inbox.extend(received[index + 1 :])
                return message
            inbox.append(message)
    raise TimeoutError("Timed out waiting for a room WebSocket message.")


def _wait_session(
    controller: RoomRealtimeController,
    agent_id: str,
    statuses: set[str],
    *,
    timeout_seconds: float,
) -> dict[str, object]:
    return _wait_until_value(
        lambda: controller.store.session("general", agent_id),
        lambda session: str(session.get("runtime_status") or "") in statuses,
        timeout_seconds=timeout_seconds,
    )


def _wait_until_value(producer, predicate, *, timeout_seconds: float):
    deadline = time.monotonic() + timeout_seconds
    latest = producer()
    while time.monotonic() < deadline:
        latest = producer()
        if predicate(latest):
            return latest
        time.sleep(0.05)
    raise TimeoutError(f"Timed out waiting for state: {latest!r}")


def _wait_until(predicate, *, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return bool(predicate())


def _resolve_executable(command: tuple[str, ...]) -> str:
    if not command:
        return ""
    executable = Path(command[0]).expanduser()
    if executable.is_absolute():
        return str(executable) if executable.is_file() else ""
    return str(shutil.which(command[0]) or "")


def _rss_kb(pid: int) -> int:
    if pid <= 0:
        return 0
    try:
        value = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)], text=True, timeout=2.0).strip()
        return int(value or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stderr_diagnostics(path: Path) -> dict[str, object]:
    try:
        data = path.read_bytes() if path.is_file() else b""
    except OSError:
        data = b""
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return {
        "stderr_byte_count": len(data),
        "stderr_line_count": len(lines),
        "stderr_warning_count": sum(1 for line in lines if re.search(r"\bwarn(?:ing)?\b", line, re.IGNORECASE)),
        "stderr_tail": "\n".join(lines[-50:])[-16000:],
    }


def _record_latency_comparison(
    result: dict[str, object],
    turns: list[dict[str, object]],
    *,
    enforce: bool,
) -> None:
    fields = (
        "provider_direct_ttfo_ms",
        "room_observed_ttfo_ms",
        "room_ttfo_extra_ms",
        "provider_direct_total_ms",
        "room_observed_total_ms",
        "room_total_extra_ms",
    )
    for field in fields:
        result[field] = [
            float(turn[field])
            for turn in turns
            if isinstance(turn.get(field), (int, float))
        ]
    result["latency_acceptance"] = _latency_acceptance(
        list(result["provider_direct_ttfo_ms"]),  # type: ignore[arg-type]
        list(result["room_observed_ttfo_ms"]),  # type: ignore[arg-type]
        enforce=enforce,
    )


def _conversation_metrics(turns: list[dict[str, object]]) -> dict[str, object]:
    ttfo = [float(turn["ttfo_ms"]) for turn in turns if isinstance(turn.get("ttfo_ms"), (int, float))]
    completed = [
        float(turn["turn_completed_ms"])
        for turn in turns
        if isinstance(turn.get("turn_completed_ms"), (int, float))
    ]
    return {
        "turn_count": len(ttfo),
        "time_to_first_agent_delta_ms": ttfo,
        "turn_completed_ms": completed,
        "p50_time_to_first_agent_delta_ms": _percentile(ttfo, 50),
        "p95_time_to_first_agent_delta_ms": _percentile(ttfo, 95),
        "p50_turn_completed_ms": _percentile(completed, 50),
        "p95_turn_completed_ms": _percentile(completed, 95),
    }


def _latency_acceptance(
    provider_direct_ttfo_ms: list[float],
    room_observed_ttfo_ms: list[float],
    *,
    enforce: bool = True,
) -> dict[str, object]:
    paired_count = min(len(provider_direct_ttfo_ms), len(room_observed_ttfo_ms))
    direct = [float(value) for value in provider_direct_ttfo_ms[:paired_count]]
    room = [float(value) for value in room_observed_ttfo_ms[:paired_count]]
    extras = [room_value - direct_value for direct_value, room_value in zip(direct, room)]
    direct_p50 = _percentile(direct, 50)
    room_p50 = _percentile(room, 50)
    extra_p50 = _percentile(extras, 50)
    extra_p95 = _percentile(extras, 95)
    ratio = round(room_p50 / direct_p50, 4) if direct_p50 and room_p50 is not None else None
    checks = {
        "has_samples": paired_count > 0,
        "p50_extra_within_300_ms": extra_p50 is not None and extra_p50 <= ROOM_TTFO_P50_EXTRA_LIMIT_MS,
        "p95_extra_within_750_ms": extra_p95 is not None and extra_p95 <= ROOM_TTFO_P95_EXTRA_LIMIT_MS,
        "room_p50_within_115_percent": ratio is not None and ratio <= ROOM_TTFO_P50_RATIO_LIMIT,
    }
    passed = all(checks.values())
    return {
        "enforced": enforce,
        "passed": passed if enforce else None,
        "reason": "strict_real_provider" if enforce else "non_strict_fixture_or_custom_provider",
        "sample_count": paired_count,
        "provider_direct_p50_ms": direct_p50,
        "room_observed_p50_ms": room_p50,
        "room_to_direct_p50_ratio": ratio,
        "p50_extra_ms": extra_p50,
        "p95_extra_ms": extra_p95,
        "limits": {
            "p50_extra_ms": ROOM_TTFO_P50_EXTRA_LIMIT_MS,
            "p95_extra_ms": ROOM_TTFO_P95_EXTRA_LIMIT_MS,
            "room_to_direct_p50_ratio": ROOM_TTFO_P50_RATIO_LIMIT,
        },
        "checks": checks,
    }


def _aggregate_metrics(results: list[dict[str, object]]) -> dict[str, object]:
    ttfo = [float(value) for result in results for value in list(result.get("ttfo_ms") or []) if isinstance(value, (int, float))]
    totals = [
        float(value)
        for result in results
        for value in list(result.get("turn_completed_ms") or [])
        if isinstance(value, (int, float))
    ]
    direct_ttfo = [
        float(value)
        for result in results
        for value in list(result.get("provider_direct_ttfo_ms") or [])
        if isinstance(value, (int, float))
    ]
    room_ttfo = [
        float(value)
        for result in results
        for value in list(result.get("room_observed_ttfo_ms") or [])
        if isinstance(value, (int, float))
    ]
    extra_ttfo = [
        float(value)
        for result in results
        for value in list(result.get("room_ttfo_extra_ms") or [])
        if isinstance(value, (int, float))
    ]
    return {
        "p50_time_to_first_agent_delta_ms": _percentile(ttfo, 50),
        "p95_time_to_first_agent_delta_ms": _percentile(ttfo, 95),
        "p50_turn_completed_ms": _percentile(totals, 50),
        "p95_turn_completed_ms": _percentile(totals, 95),
        "provider_direct_ttfo_p50_ms": _percentile(direct_ttfo, 50),
        "provider_direct_ttfo_p95_ms": _percentile(direct_ttfo, 95),
        "room_observed_ttfo_p50_ms": _percentile(room_ttfo, 50),
        "room_observed_ttfo_p95_ms": _percentile(room_ttfo, 95),
        "room_ttfo_extra_p50_ms": _percentile(extra_ttfo, 50),
        "room_ttfo_extra_p95_ms": _percentile(extra_ttfo, 95),
        "timeout_count": sum(int(result.get("timeout_count") or 0) for result in results),
    }


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 1)
    ordered = sorted(values)
    rank = (len(ordered) - 1) * (percentile / 100)
    lower = int(rank)
    upper = min(len(ordered) - 1, lower + 1)
    fraction = rank - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 1)


def _optional_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _overall_status(results: list[dict[str, object]]) -> str:
    if not results:
        return "empty"
    statuses = {str(result.get("status") or "") for result in results}
    if statuses == {"ok"}:
        return "ok"
    if statuses <= {"unavailable"}:
        return "unavailable"
    return "error"


def _write_result(output_root: str | Path, result: dict[str, object]) -> Path:
    directory = Path(output_root) / "rooms" / "general" / "smoke"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{result['run_id']}.json"
    result["result_path"] = str(path)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _now() -> str:
    return datetime.now(UTC).isoformat()
