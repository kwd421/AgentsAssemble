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
from agentsassemble.ws_room_client import WsRoomClient, connect_room_ws_with_ticket


STRICT_MESSAGE_SOURCES = {
    "codex": "codex_session_jsonl",
    "grok": "grok_chat_history",
    "antigravity": "antigravity_transcript_jsonl",
    "claude": "claude_session_jsonl",
}
TUI_NOISE = re.compile(
    r"(?:\x1b\[|Do you trust|Working\.\.\.|Thinking\.\.\.|ctrl\+|tokens?\b|permission mode|esc to|press enter)",
    re.IGNORECASE,
)


def run_room_native_cli_smoke(
    *,
    config_path: str | Path,
    output_root: str | Path = ".agentsassemble",
    providers: list[str] | None = None,
    approve_real_provider: bool = False,
    timeout_seconds: float = 180.0,
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
            for spec in specs:
                provider_result = _smoke_provider(
                    client,
                    inbox,
                    controller,
                    manager,
                    spec,
                    timeout_seconds=max(1.0, float(timeout_seconds)),
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
        expected_source = STRICT_MESSAGE_SOURCES.get(spec.agent_id)
        if not result["same_pid_over_turns"]:
            raise RuntimeError("provider CLI PID changed between turns")
        if not result["memory_marker_recalled"]:
            raise RuntimeError("provider CLI did not recall the session marker")
        if expected_source and any(source != expected_source for source in result["message_sources"]):  # type: ignore[union-attr]
            raise RuntimeError(f"provider message source was not strict {expected_source}")
        if any(TUI_NOISE.search(str(turn["event"].get("content") or "")) for turn in (first, second)):  # type: ignore[union-attr]
            raise RuntimeError("provider message contained terminal UI chrome")
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
        stderr = _stderr_diagnostics(stderr_path)
        result.update(stderr)
        errors = [
            event
            for event in controller.store.read_events("general")
            if event.get("participant_id") == spec.agent_id and event.get("type") == "error"
        ]
        if errors:
            diagnostics = errors[-1].get("diagnostics") if isinstance(errors[-1].get("diagnostics"), dict) else {}
            result["terminal_byte_count"] = int(diagnostics.get("terminal_byte_count") or 0)
            result["terminal_tail"] = str(diagnostics.get("terminal_tail") or "")[-16000:]
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
    request_id = client.command(
        "message.send",
        {"content": content},
        request_id=f"smoke-message-{agent_id}-{uuid4().hex[:8]}",
    )
    _wait_ack(client, inbox, request_id, timeout_seconds=8.0)

    def matching_event(item: dict[str, object]) -> bool:
        if item.get("op") != "event":
            return False
        return any(
            isinstance(event, dict)
            and int(event.get("seq") or 0) > before_seq
            and event.get("type") in {"message_final", "error"}
            and event.get("participant_id") == agent_id
            for event in list(item.get("events") or [])
        )

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
    return {
        "event": event,
        "session": session,
        "ttfo_ms": latency.get("ttfo_ms"),
        "total_turn_ms": latency.get("total_turn_ms") or round((time.monotonic() - started) * 1000, 1),
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


def _aggregate_metrics(results: list[dict[str, object]]) -> dict[str, object]:
    ttfo = [float(value) for result in results for value in list(result.get("ttfo_ms") or []) if isinstance(value, (int, float))]
    totals = [
        float(value)
        for result in results
        for value in list(result.get("turn_completed_ms") or [])
        if isinstance(value, (int, float))
    ]
    return {
        "p50_time_to_first_agent_delta_ms": _percentile(ttfo, 50),
        "p95_time_to_first_agent_delta_ms": _percentile(ttfo, 95),
        "p50_turn_completed_ms": _percentile(totals, 50),
        "p95_turn_completed_ms": _percentile(totals, 95),
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
