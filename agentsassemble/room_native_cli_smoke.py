from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

from agentsassemble.gui import _make_handler
from agentsassemble.live_cli_smoke import _marker_recalled
from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.room_bridge_process import NativeCliBridgeProcessManager
from agentsassemble.native_cli_providers import (
    NativeCliProviderSpec,
    native_cli_provider_spec_from_config,
)
from agentsassemble.room_realtime import RoomRealtimeController
from agentsassemble.room_routing import route_message_targets
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
ROOM_TTFO_P50_EXTRA_LIMIT_MS = 300.0
ROOM_TTFO_P95_EXTRA_LIMIT_MS = 750.0
ROOM_TTFO_P50_RATIO_LIMIT = 1.15


def run_room_native_cli_smoke(
    *,
    config_path: str | Path,
    output_root: str | Path = ".agentsassemble",
    providers: list[str] | None = None,
    approve_real_provider: bool = False,
    timeout_seconds: float = 180.0,
    latency_samples: int = 0,
    agent_conversation: bool = False,
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
    with tempfile.TemporaryDirectory(prefix="agentsassemble-native-smoke-") as temp_dir:
        server_root = Path(temp_dir) / "state"
        manager = NativeCliBridgeProcessManager(server_root)
        controller = RoomRealtimeController(server_root, providers=specs, bridge_manager=manager)
        manager.set_exit_listener(controller.bridge_process_exited)
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            _make_handler(server_root, room_realtime_controller_override=controller),
        )
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        host, port = server.server_address
        base = f"http://{host}:{port}"
        client: WsRoomClient | None = None
        inbox: list[dict[str, object]] = []
        try:
            ticket = _host_ticket(base)
            client = connect_room_ws_with_ticket(base, ticket, ["room_events"], timeout=5.0)
            client.sock.settimeout(0.2)
            _wait_message(client, inbox, lambda item: item.get("op") == "snapshot", timeout_seconds=5.0)
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
            if agent_conversation:
                conversation = _smoke_agent_conversation(
                    client,
                    inbox,
                    controller,
                    manager,
                    specs,
                    timeout_seconds=max(1.0, float(timeout_seconds)),
                )
                result["conversation"] = conversation
                result["providers"] = list(conversation.get("providers") or [])
            else:
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
        bridge_pid = int(launch.get("bridge_pid") or 0)
        result["bridge_pid"] = bridge_pid or None
        stderr_path = Path(str(launch.get("stderr_path") or ""))
        session = _wait_session(controller, spec.agent_id, {"idle", "error"}, timeout_seconds=30.0)
        if session.get("runtime_status") != "idle":
            raise RuntimeError(str(session.get("last_error") or "provider did not become idle"))
        provider_pid = int(session.get("pid") or 0)
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
        first_pid = int(first["session"].get("pid") or 0)  # type: ignore[union-attr]
        second = _run_turn(
            client,
            inbox,
            controller,
            spec.agent_id,
            f"@{spec.agent_id} 방금 기억하라고 한 AGENTSASSEMBLE_SESSION_MARKER 값을 정확히 말해.",
            previous_turn_count=1,
            timeout_seconds=timeout_seconds,
        )
        second_pid = int(second["session"].get("pid") or 0)  # type: ignore[union-attr]
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
            if int(warmup["session"].get("pid") or 0) != second_pid:  # type: ignore[union-attr]
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
                if int(turn["session"].get("pid") or 0) != second_pid:  # type: ignore[union-attr]
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
        result["alive_after_stop"] = _pid_alive(provider_pid) or _pid_alive(bridge_pid) or bool(
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
) -> dict[str, object]:
    result: dict[str, object] = {
        "status": "pending",
        "topology": "directed_ring_relay",
        "providers": [],
        "rounds": [],
        "relay_limit": controller.max_agent_relay_depth,
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
            provider_result["bridge_pid"] = int(launch.get("bridge_pid") or 0) or None
            started.append(spec)
            session = _wait_session(controller, spec.agent_id, {"idle", "error"}, timeout_seconds=30.0)
            if session.get("runtime_status") != "idle":
                raise RuntimeError(f"{spec.agent_id}: {session.get('last_error') or 'provider did not become idle'}")
            provider_pid = int(session.get("pid") or 0)
            if provider_pid <= 0:
                raise RuntimeError(f"{spec.agent_id}: provider PID was not reported")
            provider_result["provider_pids"] = [provider_pid]
            provider_result["transport"] = session.get("transport") or "unknown"
            provider_result["pty"] = bool(session.get("pty", False))

        pairs = tuple((spec, specs[(index + 1) % len(specs)]) for index, spec in enumerate(specs))
        for round_index, (source, target) in enumerate(pairs, start=1):
            marker = f"ROOM-RELAY-{round_index}-{uuid4().hex[:6]}".upper()
            round_result = _run_agent_relay_round(
                client,
                inbox,
                controller,
                source=source,
                target=target,
                marker=marker,
                timeout_seconds=timeout_seconds,
            )
            result["rounds"].append(round_result)  # type: ignore[union-attr]

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
        result["unexpected_extra_turns"] = (
            actual_turns != turn_counts_before_quiet
            or not result["sessions_quiet"]
            or any(count < 2 for count in actual_turns.values())
        )
        if result["unexpected_extra_turns"]:
            raise RuntimeError(
                "Agent relay did not settle after the configured depth limit: "
                f"before={turn_counts_before_quiet!r}, after={actual_turns!r}"
            )

        for spec in specs:
            session = controller.store.session("general", spec.agent_id)
            provider_result = provider_results[spec.agent_id]
            first_pid = int(list(provider_result["provider_pids"])[0])
            final_pid = int(session.get("pid") or 0)
            provider_result["provider_pids"] = [first_pid, final_pid]
            provider_result["same_pid_over_turns"] = bool(first_pid and first_pid == final_pid)
            provider_result["turn_count"] = int(session.get("turn_count") or 0)
            provider_result["stderr_byte_count"] = int(session.get("stderr_byte_count") or 0)
            provider_result["stderr_line_count"] = int(session.get("stderr_line_count") or 0)
            provider_result["stderr_warning_count"] = int(session.get("stderr_warning_count") or 0)
            provider_result["provider_visible_chars"] = int(session.get("provider_visible_chars") or 0)
            provider_result["provider_visible_event_count"] = int(session.get("provider_visible_event_count") or 0)
            provider_result["status"] = "ok"
            if not provider_result["same_pid_over_turns"]:
                raise RuntimeError(f"{spec.agent_id}: provider PID changed during the conversation")
        result["metrics"] = _conversation_metrics(list(result["rounds"]))  # type: ignore[arg-type]
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
            try:
                request_id = client.command(
                    "agent.stop",
                    {"agent_id": spec.agent_id},
                    request_id=f"conversation-stop-{spec.agent_id}-{uuid4().hex[:6]}",
                )
                _wait_ack(client, inbox, request_id, timeout_seconds=8.0)
            except Exception as stop_error:
                if not result.get("last_error"):
                    result["last_error"] = str(stop_error)
                    result["status"] = "error"
            _wait_until(lambda: not _pid_alive(provider_pid) and not _pid_alive(bridge_pid), timeout_seconds=5.0)
            provider_result["alive_after_stop"] = _pid_alive(provider_pid) or _pid_alive(bridge_pid) or bool(
                manager.health("general", spec.agent_id).get("running")
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


def _run_agent_relay_round(
    client: WsRoomClient,
    inbox: list[dict[str, object]],
    controller: RoomRealtimeController,
    *,
    source: NativeCliProviderSpec,
    target: NativeCliProviderSpec,
    marker: str,
    timeout_seconds: float,
) -> dict[str, object]:
    before_seq = controller.store.latest_event_sequence("general")
    source_turn_count = int(controller.store.session("general", source.agent_id).get("turn_count") or 0)
    target_turn_count = int(controller.store.session("general", target.agent_id).get("turn_count") or 0)
    started = time.monotonic()
    prompt = (
        f"@{source.agent_id} RELAY_MARKER={marker} SOURCE={source.agent_id} TARGET={target.agent_id}. "
        f"방에는 {marker}를 포함한 짧은 질문 하나만 써. 질문 안에서 상대가 답을 반드시 {marker}로 "
        "시작하고 at-sign 문자는 쓰지 않도록 요청해. 마지막에는 at-sign 문자와 TARGET id를 붙여 호출해."
    )
    request_id = client.command(
        "message.send",
        {"content": prompt},
        request_id=f"conversation-message-{source.agent_id}-{uuid4().hex[:8]}",
    )
    ack = _wait_ack(client, inbox, request_id, timeout_seconds=8.0)
    human_event_id = clean_lobby_text(
        ack.get("result", {}).get("event", {}).get("id")
        if isinstance(ack.get("result"), dict) and isinstance(ack.get("result", {}).get("event"), dict)
        else "",
        limit=128,
    )
    source_observed = _wait_agent_final_event(
        client,
        inbox,
        source.agent_id,
        after_seq=before_seq,
        observed_from=started,
        timeout_seconds=timeout_seconds,
    )
    source_final_at = time.monotonic()
    target_observed = _wait_agent_final_event(
        client,
        inbox,
        target.agent_id,
        after_seq=before_seq,
        observed_from=source_final_at,
        timeout_seconds=timeout_seconds,
    )
    target_final_at = time.monotonic()
    source_event = source_observed["event"]
    target_event = target_observed["event"]
    source_content = str(source_event.get("content") or "")
    target_content = str(target_event.get("content") or "")
    follow_up: dict[str, object] | None = None
    follow_up_count = 0
    follow_up_final_at = target_final_at
    follow_decision = route_message_targets(
        dict(target_event),
        {source.agent_id: source, target.agent_id: target},
        max_agent_relay_depth=controller.max_agent_relay_depth,
    )
    if source.agent_id in follow_decision.targets:
        follow_observed = _wait_agent_final_event(
            client,
            inbox,
            source.agent_id,
            after_seq=int(target_event.get("seq") or 0),
            observed_from=target_final_at,
            timeout_seconds=timeout_seconds,
        )
        follow_up_final_at = time.monotonic()
        follow_event = follow_observed["event"]
        follow_started = next(
            (
                event
                for event in reversed(controller.store.read_events("general"))
                if event.get("type") == "turn_started"
                and event.get("participant_id") == source.agent_id
                and event.get("turn_id") == follow_event.get("turn_id")
            ),
            {},
        )
        follow_content = str(follow_event.get("content") or "")
        expected_follow_source = STRICT_MESSAGE_SOURCES.get(source.agent_id)
        follow_checks = {
            "turn_sourced_from_target_message": follow_started.get("source_event_id") == target_event.get("id"),
            "final_sourced_from_target_message": follow_event.get("source_event_id") == target_event.get("id"),
            "relay_depth_reached_limit": int(follow_event.get("relay_depth") or 0)
            == controller.max_agent_relay_depth,
            "message_clean": not bool(TUI_NOISE.search(follow_content)),
            "message_structured": not expected_follow_source
            or follow_event.get("message_source") == expected_follow_source,
        }
        if not all(follow_checks.values()):
            failed = sorted(name for name, passed in follow_checks.items() if not passed)
            raise RuntimeError(f"Agent follow-up relay checks failed: {', '.join(failed)}")
        follow_up = {
            "agent_id": source.agent_id,
            "message_event_id": follow_event.get("id"),
            "source_event_id": follow_event.get("source_event_id"),
            "message_source": follow_event.get("message_source"),
            "output": follow_content[-2000:],
            "ttfo_ms": follow_observed["ttfo_ms"],
            "turn_completed_ms": round((follow_up_final_at - target_final_at) * 1000, 1),
            "relay_depth": follow_event.get("relay_depth"),
            "checks": follow_checks,
        }
        follow_up_count = 1

    _wait_until_value(
        lambda: controller.store.session("general", source.agent_id),
        lambda session: int(session.get("turn_count") or 0) >= source_turn_count + 1 + follow_up_count
        and session.get("runtime_status") == "idle",
        timeout_seconds=5.0,
    )
    target_session = _wait_until_value(
        lambda: controller.store.session("general", target.agent_id),
        lambda session: int(session.get("turn_count") or 0) > target_turn_count and session.get("runtime_status") == "idle",
        timeout_seconds=5.0,
    )
    target_started = next(
        (
            event
            for event in reversed(controller.store.read_events("general"))
            if event.get("type") == "turn_started"
            and event.get("participant_id") == target.agent_id
            and event.get("turn_id") == target_event.get("turn_id")
        ),
        {},
    )
    expected_source = STRICT_MESSAGE_SOURCES.get(source.agent_id)
    expected_target = STRICT_MESSAGE_SOURCES.get(target.agent_id)
    checks = {
        "human_targeted_only_source": bool(human_event_id and source_event.get("source_event_id") == human_event_id),
        "source_mentions_target": f"@{target.agent_id}" in source_content.casefold(),
        "source_carries_marker": marker.casefold() in source_content.casefold(),
        "target_carries_marker": marker.casefold() in target_content.casefold(),
        "target_turn_sourced_from_agent_message": target_started.get("source_event_id") == source_event.get("id"),
        "target_final_sourced_from_agent_message": target_event.get("source_event_id") == source_event.get("id"),
        "relay_depth_incremented": int(target_event.get("relay_depth") or 0) == 1,
        "source_message_clean": not bool(TUI_NOISE.search(source_content)),
        "target_message_clean": not bool(TUI_NOISE.search(target_content)),
        "source_message_structured": not expected_source or source_event.get("message_source") == expected_source,
        "target_message_structured": not expected_target or target_event.get("message_source") == expected_target,
    }
    if not all(checks.values()):
        failed = sorted(name for name, passed in checks.items() if not passed)
        raise RuntimeError(f"Agent relay checks failed: {', '.join(failed)}")
    return {
        "source_agent_id": source.agent_id,
        "target_agent_id": target.agent_id,
        "marker": marker,
        "human_event_id": human_event_id,
        "source_message_event_id": source_event.get("id"),
        "target_message_event_id": target_event.get("id"),
        "target_turn_source_event_id": target_started.get("source_event_id"),
        "source_message_source": source_event.get("message_source"),
        "target_message_source": target_event.get("message_source"),
        "source_output": source_content[-2000:],
        "target_output": target_content[-2000:],
        "source_ttfo_ms": source_observed["ttfo_ms"],
        "source_turn_completed_ms": round((source_final_at - started) * 1000, 1),
        "relay_ttfo_ms": target_observed["ttfo_ms"],
        "relay_turn_completed_ms": round((target_final_at - source_final_at) * 1000, 1),
        "round_total_ms": round((follow_up_final_at - started) * 1000, 1),
        "target_provider_visible_chars": target_started.get("provider_visible_chars"),
        "target_provider_visible_event_count": target_started.get("provider_visible_event_count"),
        "target_last_seen_event_id": target_session.get("last_seen_event_id"),
        "follow_up": follow_up,
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
        raise RuntimeError(str(event.get("content") or f"{agent_id} turn failed"))
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


def _conversation_metrics(rounds: list[dict[str, object]]) -> dict[str, object]:
    ttfo: list[float] = []
    completed: list[float] = []
    for round_result in rounds:
        for field in ("source_ttfo_ms", "relay_ttfo_ms"):
            value = round_result.get(field)
            if isinstance(value, (int, float)):
                ttfo.append(float(value))
        for field in ("source_turn_completed_ms", "relay_turn_completed_ms"):
            value = round_result.get(field)
            if isinstance(value, (int, float)):
                completed.append(float(value))
        follow_up = round_result.get("follow_up") if isinstance(round_result.get("follow_up"), dict) else {}
        value = follow_up.get("ttfo_ms")
        if isinstance(value, (int, float)):
            ttfo.append(float(value))
        value = follow_up.get("turn_completed_ms")
        if isinstance(value, (int, float)):
            completed.append(float(value))
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
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _now() -> str:
    return datetime.now(UTC).isoformat()
