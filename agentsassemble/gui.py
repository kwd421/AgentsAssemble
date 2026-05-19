from __future__ import annotations

import json
import math
import mimetypes
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from agentsassemble.adapters.remote_bridge import RemoteBridgeAdapter
from agentsassemble.codex_sessions import (
    build_codex_live_invite_config,
    list_codex_sessions,
    read_agent_config,
    write_agent_config,
)
from agentsassemble.config import load_agent_runtime_config, load_council_config, providers_from_config
from agentsassemble.live_agent_preflight import preflight_live_agent_config
from agentsassemble.live_agents import connect_live_agent, heartbeat_live_agent, read_live_agents, update_live_agent_engagement
from agentsassemble.live_agent_operations import append_live_agent_operation, read_live_agent_operations
from agentsassemble.live_agent_meetings import start_live_agent_meeting
from agentsassemble.live_agent_processes import LiveAgentProcessSupervisor
from agentsassemble.live_agent_probe import run_live_agent_probe, safe_probe_timeout
from agentsassemble.live_agent_rounds import build_official_round_turns, completed_official_round_ids, remaining_official_round_ids
from agentsassemble.live_agent_sessions import (
    check_live_agent_session,
    restart_live_agent_session,
    resume_live_agent_session,
    start_live_agent_session,
    stop_live_agent_session,
)
from agentsassemble.live_agent_smoke import (
    LiveAgentSmokeFailed,
    run_live_agent_official_round_smoke,
    run_live_agent_session_smoke,
    run_live_agent_smoke,
)
from agentsassemble.live_agent_turns import wait_for_official_turn_reply
from agentsassemble.live_transcript import projected_live_transcript_text
from agentsassemble.meeting import run_demo_meeting
from agentsassemble.provider_health import provider_health_report
from agentsassemble.meeting_events import (
    append_live_event,
    append_lobby_event_to_file,
    append_side_chat_event_to_file,
    clean_lobby_text,
    read_live_events,
    read_live_events_after,
    read_lobby_events,
    read_lobby_events_after,
    read_side_chat_events,
    read_side_chat_events_after,
    write_live_state,
)
from agentsassemble.adapters import default_provider_registry
from agentsassemble.models import ProviderConfig, Role

TAB_LABELS = {"lobby": "로비", "live": "실황", "board": "작전판", "archive": "아카이브"}
TABS = ["lobby", "live", "board", "archive"]
STALE_RUNNING_SECONDS = 300
SSE_ERROR_MESSAGE_LIMIT = 500
REMOTE_LOBBY_REQUESTER = None
MAX_READINESS_PROBE_AGENTS = 10
OFFICIAL_ROUND_SMOKE_ERROR = "official round smoke could not be run"
LIVE_AGENT_TURN_LOCK = threading.Lock()
MAX_LIVE_AGENT_SEQUENCE_TURNS = 12
MAX_LIVE_AGENT_ROUND_BATCH = 8
LIVE_AGENT_ROUND_SCHEDULER_LOCKS: dict[str, threading.RLock] = {}
LIVE_AGENT_ROUND_SCHEDULER_LOCKS_LOCK = threading.Lock()


def _live_agent_round_scheduler_lock(meeting_id: str) -> threading.RLock:
    with LIVE_AGENT_ROUND_SCHEDULER_LOCKS_LOCK:
        lock = LIVE_AGENT_ROUND_SCHEDULER_LOCKS.get(meeting_id)
        if lock is None:
            lock = threading.RLock()
            LIVE_AGENT_ROUND_SCHEDULER_LOCKS[meeting_id] = lock
        return lock


def list_meetings(output_root: Path, now: float | None = None) -> list[dict[str, object]]:
    meetings_dir = output_root / "meetings"
    if not meetings_dir.exists():
        return []

    meetings = []
    for meeting_dir in meetings_dir.iterdir():
        record_path = meeting_dir / "meeting.json"
        live_path = meeting_dir / "live_state.json"
        if not record_path.exists() and not live_path.exists():
            continue
        try:
            meeting, source_path, has_final_record = _load_meeting_record(meeting_dir)
        except json.JSONDecodeError:
            continue
        if _is_diagnostic_meeting_record(meeting):
            continue
        meeting = _with_inferred_live_status(
            meeting,
            meeting_dir,
            has_final_record=has_final_record,
            now=now,
        )
        stat = source_path.stat()
        meetings.append(
            {
                "meeting_id": meeting.get("meeting_id", meeting_dir.name),
                "topic": meeting.get("topic", ""),
                "question": meeting.get("question", ""),
                "created_at": meeting.get("audit_metadata", {}).get("created_at", ""),
                "live_status": meeting.get("live_status", "complete" if record_path.exists() else "unknown"),
                "path": str(meeting_dir),
                "mtime": stat.st_mtime,
            }
        )
    return sorted(meetings, key=lambda item: item["mtime"], reverse=True)


def _is_diagnostic_meeting_record(meeting: dict[str, object]) -> bool:
    return _payload_bool(meeting.get("diagnostic"))


def build_meeting_payload(meeting_dir: Path, now: float | None = None) -> dict[str, object]:
    meeting, _, has_final_record = _load_meeting_record(meeting_dir)
    meeting = _with_inferred_live_status(
        meeting,
        meeting_dir,
        has_final_record=has_final_record,
        now=now,
    )
    artifacts = {
        name: _read_optional(meeting_dir / name)
        for name in ("agenda.md", "transcript.md", "decision.md", "room-log.md", "meeting.json")
    }
    if not (meeting_dir / "transcript.md").exists() and not has_final_record:
        artifacts["transcript.md"] = projected_live_transcript_text(meeting_dir, meeting=meeting)
    tasks = {
        task_path.name: task_path.read_text(encoding="utf-8")
        for task_path in sorted((meeting_dir / "tasks").glob("*.md"))
    }
    return_packets = {
        packet_path.name: packet_path.read_text(encoding="utf-8")
        for packet_path in sorted((meeting_dir / "return_packets").glob("*.md"))
    }
    research = {}
    research_json = {}
    research_root = meeting_dir / "private_research"
    if research_root.exists():
        for research_path in sorted(research_root.glob("*/research.md")):
            research[f"{research_path.parent.name}/research.md"] = research_path.read_text(encoding="utf-8")
        for research_path in sorted(research_root.glob("*/research.json")):
            try:
                research_json[research_path.parent.name] = json.loads(research_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                research_json[research_path.parent.name] = {"error": "Research JSON could not be parsed."}
    return {
        "tabs": TABS,
        "tab_labels": TAB_LABELS,
        "meeting": meeting,
        "artifacts": artifacts,
        "tasks": tasks,
        "return_packets": return_packets,
        "research": research,
        "research_json": research_json,
        "live_events": read_live_events(meeting_dir),
    }


def _load_meeting_record(meeting_dir: Path) -> tuple[dict[str, object], Path, bool]:
    meeting_path = meeting_dir / "meeting.json"
    live_path = meeting_dir / "live_state.json"
    if meeting_path.exists():
        try:
            meeting = json.loads(meeting_path.read_text(encoding="utf-8"))
            meeting = _merge_live_progress_from_path(meeting, live_path)
            return meeting, meeting_path, True
        except json.JSONDecodeError:
            if not live_path.exists():
                raise
    return json.loads(live_path.read_text(encoding="utf-8")), live_path, False


def _with_inferred_live_status(
    meeting: dict[str, object],
    meeting_dir: Path,
    has_final_record: bool,
    now: float | None = None,
) -> dict[str, object]:
    if has_final_record or meeting.get("live_status") != "running":
        return meeting
    latest_mtime = _latest_live_mtime(meeting_dir)
    if latest_mtime is None:
        return meeting
    if (now if now is not None else time.time()) - latest_mtime < STALE_RUNNING_SECONDS:
        return meeting
    inferred = dict(meeting)
    inferred["live_status"] = "stalled"
    inferred["stalled_reason"] = "No live meeting update has been observed recently."
    inferred["last_live_update_mtime"] = latest_mtime
    return inferred


def _latest_live_mtime(meeting_dir: Path) -> float | None:
    mtimes = [
        path.stat().st_mtime
        for path in (meeting_dir / "live_state.json", meeting_dir / "live_events.jsonl")
        if path.exists()
    ]
    return max(mtimes) if mtimes else None


def serve_gui(
    host: str = "127.0.0.1",
    port: int = 8765,
    output_root: Path | None = None,
    *,
    live_agent_config: Path | None = None,
    live_agent_group_id: str = "",
    live_agent_auto_restart: bool = False,
    live_agent_max_restarts: int = 0,
    live_agent_restart_backoff_seconds: float = 5.0,
    live_agent_stale_restart_after_seconds: float = 0.0,
) -> None:
    root = output_root or Path(".agentsassemble")
    process_supervisor = LiveAgentProcessSupervisor(root)
    handler = _make_handler(root, process_supervisor=process_supervisor)
    server = ThreadingHTTPServer((host, port), handler)
    try:
        process_supervisor.start_monitor()
        server_url = _local_server_url(server.server_address)
        if live_agent_config is not None:
            _autostart_live_agent_group(
                root,
                process_supervisor,
                config_path=live_agent_config,
                server_url=server_url,
                group_id=live_agent_group_id,
                auto_restart=live_agent_auto_restart,
                max_restarts=live_agent_max_restarts,
                restart_backoff_seconds=live_agent_restart_backoff_seconds,
                stale_restart_after_seconds=live_agent_stale_restart_after_seconds,
            )
        print(f"AgentsAssemble GUI: {server_url}")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AgentsAssemble GUI")
    finally:
        process_supervisor.close()
        server.server_close()


def _autostart_live_agent_group(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    *,
    config_path: Path,
    server_url: str,
    group_id: str = "",
    auto_restart: bool = False,
    max_restarts: int = 0,
    restart_backoff_seconds: float = 5.0,
    stale_restart_after_seconds: float = 0.0,
) -> None:
    try:
        group = process_supervisor.start_group(
            config_path=config_path,
            server=server_url,
            group_id=group_id.strip() or None,
            auto_restart=auto_restart,
            max_restarts=max_restarts,
            restart_backoff_seconds=restart_backoff_seconds,
            stale_restart_after_seconds=stale_restart_after_seconds,
        )
    except Exception as error:
        record_live_agent_operation(
            output_root,
            operation="process.autostart",
            status="failed",
            target_id=group_id,
            error=str(error),
            details={
                "group_id": group_id,
                "auto_restart": bool(auto_restart),
                "max_restarts": max_restarts,
                "restart_backoff_seconds": restart_backoff_seconds,
                "stale_restart_after_seconds": stale_restart_after_seconds,
            },
        )
        print("Live-agent autostart failed; inspect recent operations for details.")
        return
    record_live_agent_operation(
        output_root,
        operation="process.autostart",
        status="success",
        target_id=str(group.get("group_id") or group_id),
        summary="autostarted live-agent process group",
        details={
            "group_id": str(group.get("group_id") or group_id),
            "group_status": str(group.get("status") or ""),
            "auto_restart": bool(auto_restart),
            "max_restarts": max_restarts,
            "restart_backoff_seconds": restart_backoff_seconds,
            "stale_restart_after_seconds": stale_restart_after_seconds,
        },
    )


def _read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def read_lobby(output_root: Path, limit: int = 80) -> list[dict[str, object]]:
    return read_lobby_events(output_root / "lobby.jsonl", limit=limit)


def append_lobby_event(output_root: Path, event: dict[str, object], *, live_agent_endpoint: bool = False) -> dict[str, object]:
    return append_lobby_event_to_file(output_root / "lobby.jsonl", event, live_agent_endpoint=live_agent_endpoint)


def read_side_chat(output_root: Path, limit: int = 120) -> list[dict[str, object]]:
    return read_side_chat_events(output_root / "side_chat.jsonl", limit=limit)


def append_side_chat_event(output_root: Path, event: dict[str, object]) -> dict[str, object]:
    return append_side_chat_event_to_file(output_root / "side_chat.jsonl", event)


def _sse_event(event_name: str, payload: dict[str, object], event_id: str | None = None) -> bytes:
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_name}")
    lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def _meeting_not_found_error(meeting_id: str) -> ValueError:
    return ValueError(f"Meeting {meeting_id} was not found.")


def _sse_stream_error_payload(stream: str, error: Exception, meeting_id: str | None = None) -> dict[str, object]:
    if stream == "meeting" and meeting_id and isinstance(error, FileNotFoundError):
        message = str(_meeting_not_found_error(meeting_id))
    else:
        message = str(error).replace("\r", " ").replace("\n", " ").strip()
    payload: dict[str, object] = {"stream": stream, "error": message[:SSE_ERROR_MESSAGE_LIMIT]}
    if meeting_id:
        payload["meeting_id"] = meeting_id
    return payload


def _stream_snapshot_payload(
    output_root: Path,
    stream: str,
    meeting_id: str | None = None,
    last_event_id: str | None = None,
) -> dict[str, object]:
    if stream == "lobby":
        events = read_lobby_events_after(output_root / "lobby.jsonl", last_event_id)
        return {"stream": "lobby", "events": events}
    if stream == "side_chat":
        events = read_side_chat_events_after(output_root / "side_chat.jsonl", last_event_id)
        return {"stream": "side_chat", "events": events}
    if stream == "meeting":
        if not meeting_id:
            raise ValueError("Meeting id is required for meeting event stream.")
        meeting_dir = output_root / "meetings" / meeting_id
        if not meeting_dir.exists():
            raise _meeting_not_found_error(meeting_id)
        try:
            events = read_live_events_after(meeting_dir, last_event_id)
        except FileNotFoundError as error:
            raise _meeting_not_found_error(meeting_id) from error
        if not meeting_dir.exists():
            raise _meeting_not_found_error(meeting_id)
        payload: dict[str, object] = {
            "stream": "meeting",
            "meeting_id": meeting_id,
            "events": events,
            "payload_signature": json.dumps(events, ensure_ascii=False, sort_keys=True),
        }
        if (meeting_dir / "meeting.json").exists():
            try:
                meeting_payload = build_meeting_payload(meeting_dir)
            except FileNotFoundError as error:
                raise _meeting_not_found_error(meeting_id) from error
            except json.JSONDecodeError:
                payload["meeting_payload_pending"] = True
                payload["payload_signature"] = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            else:
                payload["meeting_payload"] = meeting_payload
                payload["payload_signature"] = json.dumps(meeting_payload, ensure_ascii=False, sort_keys=True)
        return payload
    raise ValueError(f"Unknown event stream: {stream}")


def send_lobby_message_to_remote_bridge(
    output_root: Path,
    message: str,
    meeting_id: str | None = None,
    target_agent_id: str | None = None,
    speaker_name: str = "나",
) -> dict[str, object]:
    if not message.strip():
        raise ValueError("Message is required.")
    meeting_dir = _resolve_lobby_meeting_dir(output_root, meeting_id)
    meeting = _read_meeting_record(meeting_dir)
    role_data, binding, provider_data = _select_remote_bridge_binding(meeting, target_agent_id)
    role = _role_from_payload(role_data)
    provider = _runtime_provider_for_binding(meeting, binding, provider_data)
    session = {
        "meeting_id": meeting.get("meeting_id", meeting_dir.name),
        "agent_id": binding.get("agent_id"),
        "owner_id": binding.get("owner_id"),
        "join_mode": binding.get("join_mode"),
        "session_id": binding.get("session_id"),
    }
    adapter = RemoteBridgeAdapter(provider, requester=REMOTE_LOBBY_REQUESTER)
    remote_event = adapter.run_lobby_message(role, session, speaker_name=speaker_name, message=message.strip())
    event = {
        "name": remote_event.get("name") or role.display_name,
        "side": "other-agent",
        "kind": remote_event.get("kind") or "message",
        "message": remote_event.get("message") or "",
    }
    return append_lobby_event(output_root, event)


def provider_catalog_payload() -> dict[str, object]:
    return {"providers": default_provider_registry().catalog()}


def provider_health_payload(payload: dict[str, object]) -> dict[str, object]:
    config_path = str(payload.get("config_path") or "").strip()
    if not config_path:
        raise ValueError("Provider health requires config_path.")
    probe_mode = str(payload.get("probe_mode") or "none").strip() or "none"
    probe_timeout_value = payload.get("probe_timeout_seconds", payload.get("probe_timeout", 2.0))
    try:
        probe_timeout = float(probe_timeout_value)
    except (TypeError, ValueError) as error:
        raise ValueError("Provider health probe_timeout_seconds must be a finite non-negative number.") from error
    if not math.isfinite(probe_timeout) or probe_timeout < 0:
        raise ValueError("Provider health probe_timeout_seconds must be a finite non-negative number.")
    return provider_health_report(
        Path(config_path),
        probe_mode=probe_mode,
        probe_timeout_seconds=probe_timeout,
    )


def codex_sessions_payload(limit: int = 20) -> dict[str, object]:
    return {"sessions": list_codex_sessions(limit=limit)}


def live_agents_payload(output_root: Path) -> dict[str, object]:
    return {"agents": read_live_agents(output_root)}


def live_agent_operations_payload(output_root: Path, *, limit: int = 50) -> dict[str, object]:
    return {"operations": read_live_agent_operations(output_root, limit=limit)}


def live_agent_meeting_start_payload(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    council_config_path = str(payload.get("council_config_path") or payload.get("council_config") or "").strip()
    agent_config_path = str(payload.get("agent_config_path") or payload.get("agent_config") or "").strip()
    return start_live_agent_meeting(
        output_root,
        council_config_path=Path(council_config_path) if council_config_path else None,
        agent_config_path=Path(agent_config_path) if agent_config_path else None,
        meeting_id=str(payload.get("meeting_id") or ""),
    )


def live_agent_session_start_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    council_config_path = str(payload.get("council_config_path") or payload.get("council_config") or "").strip()
    agent_config_path = str(payload.get("agent_config_path") or payload.get("agent_config") or "").strip()
    live_agent_config_path = str(payload.get("live_agent_config_path") or payload.get("live_agent_config") or "").strip()
    if not live_agent_config_path:
        raise ValueError("Live agent config path is required.")
    session = start_live_agent_session(
        output_root,
        process_supervisor,
        server=str(payload.get("server") or default_server),
        council_config_path=Path(council_config_path) if council_config_path else None,
        agent_config_path=Path(agent_config_path) if agent_config_path else None,
        live_agent_config_path=Path(live_agent_config_path),
        meeting_id=str(payload.get("meeting_id") or ""),
        group_id=str(payload.get("group_id") or ""),
        connect_timeout_seconds=_payload_nonnegative_float(payload.get("connect_timeout_seconds"), 5.0),
        auto_restart=_payload_bool(payload.get("auto_restart")),
        max_restarts=_payload_nonnegative_int(payload.get("max_restarts"), 0),
        restart_backoff_seconds=_payload_nonnegative_float(payload.get("restart_backoff_seconds"), 5.0),
        stale_restart_after_seconds=_payload_nonnegative_float(payload.get("stale_restart_after_seconds"), 0.0),
        diagnostic=_payload_bool(payload.get("diagnostic")),
    )
    if _payload_bool(payload.get("run_remaining_rounds")):
        auto_rounds_options = _session_auto_rounds_options(payload)
        if _operation_result_status(session.get("status")) == "ready":
            session["auto_rounds"] = live_agent_turn_rounds_payload(
                output_root,
                str(session.get("meeting_id") or ""),
                auto_rounds_options,
            )
        else:
            session["auto_rounds"] = _skipped_session_auto_rounds_result(session, auto_rounds_options)
    return session


def live_agent_session_resume_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    live_agent_config_path = str(payload.get("live_agent_config_path") or payload.get("live_agent_config") or "").strip()
    if not live_agent_config_path:
        raise ValueError("Live agent config path is required.")
    session = resume_live_agent_session(
        output_root,
        process_supervisor,
        server=str(payload.get("server") or default_server),
        live_agent_config_path=Path(live_agent_config_path),
        meeting_id=str(payload.get("meeting_id") or ""),
        group_id=str(payload.get("group_id") or ""),
        connect_timeout_seconds=_payload_nonnegative_float(payload.get("connect_timeout_seconds"), 5.0),
        auto_restart=_payload_bool(payload.get("auto_restart")),
        max_restarts=_payload_nonnegative_int(payload.get("max_restarts"), 0),
        restart_backoff_seconds=_payload_nonnegative_float(payload.get("restart_backoff_seconds"), 5.0),
        stale_restart_after_seconds=_payload_nonnegative_float(payload.get("stale_restart_after_seconds"), 0.0),
    )
    if _payload_bool(payload.get("run_remaining_rounds")):
        auto_rounds_options = _session_auto_rounds_options(payload)
        if _operation_result_status(session.get("status")) == "ready":
            session["auto_rounds"] = live_agent_turn_rounds_payload(
                output_root,
                str(session.get("meeting_id") or ""),
                auto_rounds_options,
            )
        else:
            session["auto_rounds"] = _skipped_session_auto_rounds_result(session, auto_rounds_options)
    return session


def live_agent_session_check_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
) -> dict[str, object]:
    group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        raise ValueError("Live agent group id is required.")
    return check_live_agent_session(
        output_root,
        process_supervisor,
        meeting_id=str(payload.get("meeting_id") or ""),
        group_id=group_id,
    )


def live_agent_session_restart_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
) -> dict[str, object]:
    group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        raise ValueError("Live agent group id is required.")
    return restart_live_agent_session(
        output_root,
        process_supervisor,
        meeting_id=str(payload.get("meeting_id") or ""),
        group_id=group_id,
        connect_timeout_seconds=_payload_nonnegative_float(payload.get("connect_timeout_seconds"), 5.0),
    )


def live_agent_session_stop_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
) -> dict[str, object]:
    group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        raise ValueError("Live agent group id is required.")
    return stop_live_agent_session(
        output_root,
        process_supervisor,
        meeting_id=str(payload.get("meeting_id") or ""),
        group_id=group_id,
    )


def _session_auto_rounds_options(payload: dict[str, object]) -> dict[str, object]:
    return {
        "timeout_seconds": _payload_nonnegative_float(
            payload.get("round_timeout_seconds", payload.get("timeout_seconds", payload.get("timeout"))),
            30.0,
        ),
        "max_rounds": _payload_bounded_round_count(payload.get("round_max_rounds", payload.get("max_rounds"))),
        "stop_on_timeout": _payload_bool(payload.get("round_stop_on_timeout", payload.get("stop_on_timeout"))),
    }


def _skipped_session_auto_rounds_result(session: dict[str, object], options: dict[str, object]) -> dict[str, object]:
    return {
        "status": "skipped",
        "reason": "session_not_ready",
        "meeting_id": clean_lobby_text(session.get("meeting_id"), limit=128),
        "round_count": 0,
        "answered_round_count": 0,
        "completed_round_count": 0,
        "timeout_round_count": 0,
        "skipped_round_count": 0,
        "stopped_round_count": 0,
        "stopped": False,
        "stop_on_timeout": _payload_bool(options.get("stop_on_timeout")),
        "timeout_seconds": _payload_nonnegative_float(options.get("timeout_seconds"), 0.0),
        "max_rounds": _payload_bounded_round_count(options.get("max_rounds")),
        "results": [],
    }


def connect_live_agent_payload(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    return {"agent": connect_live_agent(output_root, payload), "agents": read_live_agents(output_root)}


def update_live_agent_engagement_payload(output_root: Path, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
    agent = update_live_agent_engagement(output_root, agent_id, str(payload.get("engagement_mode") or ""))
    return {"agent": agent, "agents": read_live_agents(output_root)}


def live_agent_room_payload(output_root: Path, agent_id: str) -> dict[str, object]:
    agent = _live_agent_for_id(output_root, agent_id)
    meeting_id = str(agent.get("meeting_id") or "").strip()
    live_events = []
    if meeting_id:
        meeting_dir = _safe_meeting_dir(output_root, meeting_id)
        if meeting_dir.exists():
            live_events = _live_events_visible_to_agent(read_live_events(meeting_dir), agent_id)
    return {
        "agent": agent,
        "agents": read_live_agents(output_root),
        "meetings": list_meetings(output_root),
        "meeting_id": meeting_id,
        "live_events": live_events,
        "lobby_events": read_lobby(output_root),
        "side_chat_events": read_side_chat(output_root),
    }


def live_agent_heartbeat_payload(output_root: Path, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
    agent = heartbeat_live_agent(output_root, agent_id, status=str(payload.get("status") or "online"), metadata=payload)
    return {"agent": agent, "agents": read_live_agents(output_root)}


def live_agent_lobby_message_payload(output_root: Path, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
    agent = heartbeat_live_agent(output_root, agent_id, status="online")
    message = str(payload.get("message") or "").strip()
    if not message:
        raise ValueError("Message is required.")
    event = append_lobby_event(
        output_root,
        {
            "name": agent.get("display_name") or agent.get("agent_id") or agent_id,
            "side": "other-agent",
            "kind": payload.get("kind") or "message",
            "message": message,
            "actor_id": agent.get("agent_id") or agent_id,
            "source_event_id": payload.get("source_event_id") or "",
            "auto_chain_depth": payload.get("auto_chain_depth") or 0,
        },
        live_agent_endpoint=True,
    )
    return {"agent": agent, "event": event, "events": read_lobby(output_root)}


def live_agent_turn_request_payload(output_root: Path, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    meeting_dir = _safe_meeting_dir(output_root, clean_meeting_id)
    if not clean_meeting_id or not meeting_dir.exists():
        raise ValueError(f"Meeting {clean_meeting_id or '(blank)'} was not found.")
    agent_id = clean_lobby_text(payload.get("agent_id"), limit=64)
    if not agent_id:
        raise ValueError("Agent id is required.")
    agent = _live_agent_for_id(output_root, agent_id)
    agent_meeting_id = str(agent.get("meeting_id") or "").strip()
    if agent_meeting_id != clean_meeting_id:
        raise ValueError(f"Live agent {agent_id} is not attached to meeting {clean_meeting_id}.")
    content = clean_lobby_text(payload.get("content") or payload.get("message"), limit=4000)
    if not content:
        raise ValueError("Official turn request content is required.")
    role_id = clean_lobby_text(payload.get("role_id"), limit=128) or agent_id
    display_name = clean_lobby_text(payload.get("display_name"), limit=64) or str(agent.get("display_name") or agent_id)
    event = append_live_event(
        meeting_dir,
        {
            "kind": "live_agent_turn_request",
            "meeting_id": clean_meeting_id,
            "actor_id": "moderator",
            "target_agent_id": agent_id,
            "role_id": role_id,
            "display_name": display_name,
            "audience": f"agent:{agent_id}",
            "content": content,
            "turn_id": clean_lobby_text(payload.get("turn_id"), limit=128),
            "turn_index": _payload_optional_int(payload.get("turn_index")),
            "engagement_mode": "moderator_called",
        },
    )
    return {"agent": agent, "event": event, "live_events": read_live_events(meeting_dir)}


def live_agent_turn_call_payload(output_root: Path, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
    turn_request = live_agent_turn_request_payload(output_root, meeting_id, payload)
    request_event = turn_request.get("event") if isinstance(turn_request.get("event"), dict) else {}
    agent = turn_request.get("agent") if isinstance(turn_request.get("agent"), dict) else {}
    clean_meeting_id = clean_lobby_text(request_event.get("meeting_id") or meeting_id, limit=128)
    meeting_dir = _safe_meeting_dir(output_root, clean_meeting_id)
    agent_id = clean_lobby_text(request_event.get("target_agent_id") or payload.get("agent_id"), limit=64)
    source_event_id = clean_lobby_text(request_event.get("id"), limit=128)
    if not agent_id or not source_event_id:
        raise ValueError("Official turn request could not be created.")
    wait_result = wait_for_official_turn_reply(
        meeting_dir,
        agent_id=agent_id,
        source_event_id=source_event_id,
        timeout_seconds=_payload_nonnegative_float(payload.get("timeout_seconds", payload.get("timeout")), 30.0),
    )
    return {
        "status": wait_result["status"],
        "agent": agent,
        "request_event": request_event,
        "reply_event": wait_result["reply_event"],
        "elapsed_seconds": wait_result["elapsed_seconds"],
        "timeout_seconds": wait_result["timeout_seconds"],
        "live_events": _live_events_visible_to_agent(read_live_events(meeting_dir), agent_id),
    }


def live_agent_turn_sequence_payload(output_root: Path, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
    turns = _payload_turn_sequence(payload.get("turns"))
    clean_meeting_id = _validate_live_agent_turn_sequence(output_root, meeting_id, turns)
    timeout_seconds = _payload_nonnegative_float(payload.get("timeout_seconds", payload.get("timeout")), 30.0)
    stop_on_timeout = _payload_bool(payload.get("stop_on_timeout"))
    results = []
    stopped = False
    for index, turn in enumerate(turns):
        turn_payload = dict(turn)
        turn_payload.setdefault("timeout_seconds", timeout_seconds)
        if turn_payload.get("turn_index") is None:
            turn_payload["turn_index"] = index
        result = live_agent_turn_call_payload(output_root, meeting_id, turn_payload)
        sequence_result = _live_agent_turn_sequence_result(index, result)
        results.append(sequence_result)
        if sequence_result["status"] != "answered" and stop_on_timeout:
            stopped = True
            results.extend(_skipped_turn_sequence_results(turns[index + 1 :], start_index=index + 1))
            break
    answered_count = sum(1 for result in results if result["status"] == "answered")
    timeout_count = sum(1 for result in results if result["status"] == "timeout")
    skipped_count = sum(1 for result in results if result["status"] == "skipped")
    return {
        "status": _live_agent_turn_sequence_status(answered_count, timeout_count, skipped_count),
        "meeting_id": clean_meeting_id,
        "turn_count": len(turns),
        "answered_count": answered_count,
        "timeout_count": timeout_count,
        "skipped_count": skipped_count,
        "stopped": stopped,
        "stop_on_timeout": stop_on_timeout,
        "timeout_seconds": timeout_seconds,
        "results": results,
    }


def live_agent_turn_round_payload(output_root: Path, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    meeting_dir = _safe_meeting_dir(output_root, clean_meeting_id)
    if not clean_meeting_id or not meeting_dir.exists():
        raise ValueError(f"Meeting {clean_meeting_id or '(blank)'} was not found.")
    with _live_agent_round_scheduler_lock(clean_meeting_id):
        return _live_agent_turn_round_payload_locked(output_root, clean_meeting_id, meeting_dir, payload)


def _live_agent_turn_round_payload_locked(
    output_root: Path,
    clean_meeting_id: str,
    meeting_dir: Path,
    payload: dict[str, object],
) -> dict[str, object]:
    meeting = _read_meeting_record(meeting_dir)
    round_id = clean_lobby_text(payload.get("round_id"), limit=128)
    if round_id in completed_official_round_ids(meeting):
        return _completed_official_round_result(clean_meeting_id, round_id)
    round_turns = build_official_round_turns(
        meeting,
        read_live_agents(output_root),
        meeting_id=clean_meeting_id,
        round_id=round_id,
        instruction=payload.get("content") or payload.get("instruction") or payload.get("message"),
        role_ids=_payload_role_ids(payload.get("role_ids")),
        max_turns=MAX_LIVE_AGENT_SEQUENCE_TURNS,
    )
    sequence = live_agent_turn_sequence_payload(
        output_root,
        clean_meeting_id,
        {
            "turns": round_turns["turns"],
            "timeout_seconds": _payload_nonnegative_float(payload.get("timeout_seconds", payload.get("timeout")), 30.0),
            "stop_on_timeout": _payload_bool(payload.get("stop_on_timeout")),
        },
    )
    result = dict(sequence)
    result["round_id"] = round_turns["round_id"]
    result["role_ids"] = round_turns["role_ids"]
    _record_answered_official_round_progress(meeting_dir, result)
    return result


def live_agent_turn_rounds_payload(output_root: Path, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    meeting_dir = _safe_meeting_dir(output_root, clean_meeting_id)
    if not clean_meeting_id or not meeting_dir.exists():
        raise ValueError(f"Meeting {clean_meeting_id or '(blank)'} was not found.")
    max_rounds = _payload_bounded_round_count(payload.get("max_rounds"))
    timeout_seconds = _payload_nonnegative_float(payload.get("timeout_seconds", payload.get("timeout")), 30.0)
    stop_on_timeout = _payload_bool(payload.get("stop_on_timeout"))
    with _live_agent_round_scheduler_lock(clean_meeting_id):
        meeting = _read_meeting_record(meeting_dir)
        round_ids = remaining_official_round_ids(meeting, max_rounds=max_rounds)
        return _live_agent_turn_rounds_payload_locked(
            output_root,
            clean_meeting_id,
            round_ids,
            timeout_seconds=timeout_seconds,
            stop_on_timeout=stop_on_timeout,
            max_rounds=max_rounds,
        )


def _live_agent_turn_rounds_payload_locked(
    output_root: Path,
    clean_meeting_id: str,
    round_ids: list[str],
    *,
    timeout_seconds: float,
    stop_on_timeout: bool,
    max_rounds: int,
) -> dict[str, object]:
    results = []
    stopped = False
    for index, round_id in enumerate(round_ids):
        if stopped:
            results.append(_skipped_round_result(index, round_id, timeout_seconds))
            continue
        round_result = live_agent_turn_round_payload(
            output_root,
            clean_meeting_id,
            {
                "round_id": round_id,
                "timeout_seconds": timeout_seconds,
                "stop_on_timeout": stop_on_timeout,
            },
        )
        summary = _live_agent_round_batch_result(index, round_result)
        results.append(summary)
        if summary["status"] != "answered" and stop_on_timeout:
            stopped = True
    answered_count = sum(1 for result in results if result["status"] == "answered")
    completed_count = sum(1 for result in results if result["status"] == "complete")
    timeout_count = sum(1 for result in results if result["status"] == "timeout")
    skipped_count = sum(1 for result in results if result["status"] == "skipped")
    stopped_count = sum(1 for result in results if result["status"] == "stopped")
    return {
        "status": _live_agent_round_batch_status(answered_count, completed_count, timeout_count, skipped_count, stopped_count, len(results)),
        "meeting_id": clean_meeting_id,
        "round_count": len(results),
        "answered_round_count": answered_count,
        "completed_round_count": completed_count,
        "timeout_round_count": timeout_count,
        "skipped_round_count": skipped_count,
        "stopped_round_count": stopped_count,
        "stopped": stopped,
        "stop_on_timeout": stop_on_timeout,
        "timeout_seconds": timeout_seconds,
        "max_rounds": max_rounds,
        "results": results,
    }


def _record_answered_official_round_progress(meeting_dir: Path, round_result: dict[str, object]) -> None:
    if round_result.get("status") != "answered":
        return
    round_id = clean_lobby_text(round_result.get("round_id"), limit=128)
    if not round_id:
        return
    meeting = _read_meeting_record(meeting_dir)
    progress = {
        "id": round_id,
        "status": "answered",
        "role_ids": _safe_payload_role_ids(round_result.get("role_ids")),
        "turn_count": _payload_nonnegative_int(round_result.get("turn_count"), 0),
        "answered_count": _payload_nonnegative_int(round_result.get("answered_count"), 0),
        "timeout_count": _payload_nonnegative_int(round_result.get("timeout_count"), 0),
        "skipped_count": _payload_nonnegative_int(round_result.get("skipped_count"), 0),
    }
    updated_rounds = []
    replaced = False
    for item in _as_dict_list(meeting.get("debate_rounds")):
        item_round_id = clean_lobby_text(item.get("id") or item.get("round"), limit=128)
        if item_round_id == round_id:
            if not replaced:
                merged = dict(item)
                merged.update(progress)
                updated_rounds.append(merged)
                replaced = True
            continue
        updated_rounds.append(item)
    if not replaced:
        updated_rounds.append(progress)
    meeting["debate_rounds"] = updated_rounds
    write_live_state(meeting_dir, meeting)


def _completed_official_round_result(meeting_id: str, round_id: str) -> dict[str, object]:
    return {
        "status": "complete",
        "meeting_id": meeting_id,
        "round_id": round_id,
        "role_ids": [],
        "turn_count": 0,
        "answered_count": 0,
        "timeout_count": 0,
        "skipped_count": 0,
        "stopped": False,
        "stop_on_timeout": False,
        "timeout_seconds": 0.0,
        "results": [],
    }


def _payload_bounded_round_count(value: object) -> int:
    requested = _payload_nonnegative_int(value, MAX_LIVE_AGENT_ROUND_BATCH)
    if requested <= 0:
        return MAX_LIVE_AGENT_ROUND_BATCH
    return min(requested, MAX_LIVE_AGENT_ROUND_BATCH)


def _live_agent_round_batch_result(index: int, round_result: dict[str, object]) -> dict[str, object]:
    return {
        "index": index,
        "round_id": clean_lobby_text(round_result.get("round_id"), limit=128),
        "status": str(round_result.get("status") or "unknown"),
        "role_ids": _safe_payload_role_ids(round_result.get("role_ids")),
        "turn_count": _payload_nonnegative_int(round_result.get("turn_count"), 0),
        "answered_count": _payload_nonnegative_int(round_result.get("answered_count"), 0),
        "timeout_count": _payload_nonnegative_int(round_result.get("timeout_count"), 0),
        "skipped_count": _payload_nonnegative_int(round_result.get("skipped_count"), 0),
    }


def _skipped_round_result(index: int, round_id: str, timeout_seconds: float) -> dict[str, object]:
    return {
        "index": index,
        "round_id": clean_lobby_text(round_id, limit=128),
        "status": "skipped",
        "role_ids": [],
        "turn_count": 0,
        "answered_count": 0,
        "timeout_count": 0,
        "skipped_count": 0,
        "timeout_seconds": timeout_seconds,
    }


def _live_agent_round_batch_status(
    answered_count: int,
    completed_count: int,
    timeout_count: int,
    skipped_count: int,
    stopped_count: int,
    round_count: int,
) -> str:
    if round_count == 0:
        return "complete"
    if answered_count == round_count:
        return "answered"
    if answered_count + completed_count == round_count:
        return "answered" if answered_count else "complete"
    if stopped_count or skipped_count:
        return "stopped"
    if timeout_count:
        return "timeout"
    return "degraded"


def live_agent_official_turn_payload(output_root: Path, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
    agent = _live_agent_for_id(output_root, agent_id)
    meeting_id = clean_lobby_text(payload.get("meeting_id") or agent.get("meeting_id"), limit=128)
    agent_meeting_id = clean_lobby_text(agent.get("meeting_id"), limit=128)
    if not agent_meeting_id or meeting_id != agent_meeting_id:
        raise ValueError(f"Live agent {agent_id} is not attached to meeting {meeting_id or '(blank)'}.")
    meeting_dir = _safe_meeting_dir(output_root, meeting_id)
    if not meeting_id or not meeting_dir.exists():
        raise ValueError(f"Meeting {meeting_id or '(blank)'} was not found.")
    content = clean_lobby_text(payload.get("content") or payload.get("message"), limit=4000)
    if not content:
        raise ValueError("Official turn content is required.")
    source_event_id = clean_lobby_text(payload.get("source_event_id"), limit=128)
    if not source_event_id:
        raise ValueError("Official turn source_event_id is required.")
    with LIVE_AGENT_TURN_LOCK:
        request_event = _matching_live_agent_turn_request(meeting_dir, agent_id, source_event_id)
        if request_event is None:
            raise ValueError("Matching official turn request was not found.")
        existing_reply = _official_turn_reply_for_request(meeting_dir, agent_id, source_event_id)
        if existing_reply is not None:
            event = existing_reply
        else:
            role_id = clean_lobby_text(request_event.get("role_id"), limit=128) or agent_id
            display_name = (
                clean_lobby_text(request_event.get("display_name"), limit=64)
                or clean_lobby_text(agent.get("display_name"), limit=64)
                or agent_id
            )
            request_turn_index = request_event.get("turn_index")
            turn_index = request_turn_index if isinstance(request_turn_index, int) and not isinstance(request_turn_index, bool) else None
            event = append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": meeting_id,
                    "actor_id": agent_id,
                    "target_agent_id": agent_id,
                    "source_event_id": source_event_id,
                    "role_id": role_id,
                    "display_name": display_name,
                    "content": content,
                    "turn_id": clean_lobby_text(request_event.get("turn_id"), limit=128),
                    "turn_index": turn_index,
                    "engagement_mode": "moderator_called",
                },
            )
    updated_agent = heartbeat_live_agent(
        output_root,
        agent_id,
        status="online",
        metadata={"last_reply_at": datetime.now(UTC).isoformat(), "last_observed_live_event_id": source_event_id},
    )
    return {
        "agent": updated_agent,
        "event": event,
        "live_events": _live_events_visible_to_agent(read_live_events(meeting_dir), agent_id),
    }


def live_agent_probe_payload(output_root: Path, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
    timeout_seconds = safe_probe_timeout(_payload_nonnegative_float(payload.get("timeout_seconds", payload.get("timeout")), 12.0))
    return run_live_agent_probe(
        output_root,
        agent_id,
        timeout_seconds=timeout_seconds,
    )


def live_agent_processes_payload(
    process_supervisor: LiveAgentProcessSupervisor,
    *,
    output_root: Path | None = None,
) -> dict[str, object]:
    groups = process_supervisor.list_groups()
    if output_root is None:
        return {"groups": groups}
    return {"groups": _groups_with_agent_connection_evidence(groups, read_live_agents(output_root))}


def live_agent_preflight_payload(payload: dict[str, object], *, default_server: str) -> dict[str, object]:
    config_path = Path(str(payload.get("config_path") or "configs/live-agents.example.json"))
    server = str(payload.get("server") or default_server)
    return preflight_live_agent_config(config_path, server_override=server)


def live_agent_smoke_payload(payload: dict[str, object], *, default_server: str) -> dict[str, object]:
    return run_live_agent_smoke(
        server=default_server,
        group_id=str(payload.get("group_id") or ""),
        timeout_seconds=_payload_nonnegative_float(payload.get("timeout"), 12.0),
        request_json=_request_json,
    )


def live_agent_official_round_smoke_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    return run_live_agent_official_round_smoke(
        output_root=output_root,
        server=default_server,
        group_id=str(payload.get("group_id") or ""),
        timeout_seconds=_payload_nonnegative_float(payload.get("timeout"), 12.0),
        request_json=_request_json,
    )


def live_agent_session_smoke_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    return run_live_agent_session_smoke(
        server=default_server,
        group_id=str(payload.get("group_id") or ""),
        meeting_id=str(payload.get("meeting_id") or ""),
        timeout_seconds=_payload_nonnegative_float(payload.get("timeout"), 12.0),
        request_json=_request_json,
        output_root=output_root,
    )


def live_agent_readiness_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    health = live_agent_health_payload(output_root, process_supervisor)
    checks = [{"id": "health", "status": health.get("status") or "unknown"}]
    invalid_probe_payload = _invalid_probe_id_payload(payload.get("probe_agent_ids")) or _invalid_probe_id_payload(
        payload.get("probe_group_ids")
    )
    probe_plan = _readiness_probe_plan(
        process_supervisor.snapshot_groups(),
        requested_agent_ids=_payload_probe_agent_ids(payload.get("probe_agent_ids")),
        requested_group_ids=_payload_probe_group_ids(payload.get("probe_group_ids")),
    )
    probe_agent_ids = list(probe_plan["agent_ids"])
    probe_groups = list(probe_plan["probe_groups"])
    probe_timeout = safe_probe_timeout(_payload_nonnegative_float(payload.get("probe_timeout_seconds", payload.get("timeout")), 12.0))
    probe_error = ""
    official_round_requested = _payload_bool(payload.get("official_round_smoke"))
    if invalid_probe_payload:
        probe_error = "Invalid probe id payload; expected a list of strings."
    elif len(probe_agent_ids) > MAX_READINESS_PROBE_AGENTS:
        probe_error = f"Too many probe agents requested; maximum is {MAX_READINESS_PROBE_AGENTS}."
    try:
        smoke = _safe_readiness_smoke_result(live_agent_smoke_payload(payload, default_server=default_server))
    except LiveAgentSmokeFailed as error:
        smoke = _safe_readiness_smoke_result(
            {
                "status": "failed",
                "group_id": str(payload.get("group_id") or ""),
                "error": str(error),
            }
        )
    checks.append({"id": "smoke", "status": smoke.get("status") or "unknown"})
    official_round_smoke: dict[str, object] = {}
    if official_round_requested and smoke.get("status") == "ok":
        try:
            official_round_smoke = _safe_readiness_official_round_smoke_result(
                live_agent_official_round_smoke_payload(output_root, payload, default_server=default_server)
            )
        except (LiveAgentSmokeFailed, ValueError, urllib.error.URLError):
            official_round_smoke = _safe_readiness_official_round_smoke_result(
                {
                    "status": "failed",
                    "group_id": str(payload.get("group_id") or ""),
                    "error": OFFICIAL_ROUND_SMOKE_ERROR,
                }
            )
        checks.append({"id": "official_round_smoke", "status": official_round_smoke.get("status") or "unknown"})
    elif official_round_requested:
        official_round_smoke = {
            "status": "skipped",
            "group_id": str(payload.get("group_id") or ""),
            "reason": "smoke did not pass",
        }
        checks.append({"id": "official_round_smoke", "status": "skipped"})
    probes: list[dict[str, object]] = []
    probe_group_failed = any(group.get("status") != "ok" for group in probe_groups)
    if smoke.get("status") == "ok":
        for group in probe_groups:
            checks.append({"id": f"probe_group:{group.get('group_id') or 'unknown'}", "status": group.get("status") or "unknown"})
    if smoke.get("status") == "ok" and (probe_error or probe_group_failed):
        if probe_error:
            check_id = "probe_request_payload" if invalid_probe_payload else "probe_request_limit"
            checks.append({"id": check_id, "status": "failed"})
    elif smoke.get("status") == "ok":
        for agent_id in probe_agent_ids:
            try:
                probe = run_live_agent_probe(output_root, agent_id, timeout_seconds=probe_timeout)
            except ValueError:
                probe = {"status": "failed", "agent_id": agent_id, "reason": "probe could not be run"}
            safe_probe = _safe_readiness_probe_result(probe)
            probes.append(safe_probe)
            checks.append({"id": f"probe:{agent_id}", "status": safe_probe.get("status") or "unknown"})
    if smoke.get("status") != "ok":
        status = "failed"
    elif official_round_requested and official_round_smoke.get("status") != "ok":
        status = "failed"
    elif probe_group_failed:
        status = "failed"
    elif probe_error:
        status = "failed"
    elif any(probe.get("status") != "ok" for probe in probes):
        status = "failed"
    elif health.get("status") != "ok":
        status = "degraded"
    else:
        status = "ready"
    result = {"status": status, "checks": checks, "health": health, "smoke": smoke}
    if official_round_smoke:
        result["official_round_smoke"] = official_round_smoke
    if probe_error:
        result["probe_error"] = probe_error
    if probe_groups:
        result["probe_groups"] = _safe_readiness_probe_groups(probe_groups, include_agent_ids=not probe_error)
    if probe_agent_ids and not probe_error and not probe_group_failed:
        result["effective_probe_agent_ids"] = probe_agent_ids
    if probes:
        result["probes"] = probes
    return result


def live_agent_health_payload(output_root: Path, process_supervisor: LiveAgentProcessSupervisor) -> dict[str, object]:
    agents = read_live_agents(output_root)
    groups = process_supervisor.snapshot_groups()
    diagnostic_group_ids = _diagnostic_agent_group_ids(agents)
    agent_summary = _live_agent_health_summary(agents)
    process_summary = _live_agent_process_health_summary(groups, diagnostic_group_ids=diagnostic_group_ids)
    connection_summary = _live_agent_connection_health_summary(
        groups,
        agents,
        diagnostic_group_ids=diagnostic_group_ids,
    )
    status = "degraded" if agent_summary["attention"] or process_summary["attention"] or connection_summary["attention"] else "ok"
    return {"status": status, "agents": agent_summary, "processes": process_summary, "connections": connection_summary}


def _live_agent_health_summary(agents: list[dict[str, object]]) -> dict[str, object]:
    agents = [agent for agent in agents if not _is_diagnostic_agent(agent)]
    counts = {"online": 0, "working": 0, "error": 0, "stale": 0, "offline": 0}
    attention = []
    for index, agent in enumerate(agents, start=1):
        raw_status = str(agent.get("status") or "offline")
        status = raw_status if raw_status in counts else "offline"
        counts[status] += 1
        if status in {"error", "stale", "offline"}:
            attention.append(str(agent.get("agent_id") or f"missing-agent-id-{index}"))
    return {"total": len(agents), "live": counts["online"] + counts["working"], "counts": counts, "attention": attention}


def _live_agent_process_health_summary(
    groups: list[dict[str, object]],
    *,
    diagnostic_group_ids: set[str] | None = None,
) -> dict[str, object]:
    diagnostic_group_ids = diagnostic_group_ids or set()
    groups = [group for group in groups if not _is_diagnostic_process_group(group, diagnostic_group_ids)]
    counts = {"running": 0, "restarting": 0, "error": 0, "unknown": 0, "stopped": 0}
    attention = []
    meeting_ids = {}
    for index, group in enumerate(groups, start=1):
        raw_status = str(group.get("status") or "unknown")
        status = raw_status if raw_status in counts else "unknown"
        counts[status] += 1
        group_id = str(group.get("group_id") or f"missing-process-group-id-{index}")
        meeting_id = _safe_process_meeting_id(group.get("meeting_id"))
        if group_id and meeting_id:
            meeting_ids[group_id] = meeting_id
        if status in {"restarting", "error", "unknown", "stopped"}:
            attention.append(group_id)
    return {"total": len(groups), "counts": counts, "attention": attention, "meeting_ids": meeting_ids}


def _live_agent_connection_health_summary(
    groups: list[dict[str, object]],
    agents: list[dict[str, object]],
    *,
    diagnostic_group_ids: set[str] | None = None,
) -> dict[str, object]:
    diagnostic_group_ids = diagnostic_group_ids or set()
    visible_agents = [agent for agent in agents if not _is_diagnostic_agent(agent)]
    expected = 0
    connected = 0
    attention = []
    for group in groups:
        if str(group.get("status") or "") != "running":
            continue
        if _is_diagnostic_process_group(group, diagnostic_group_ids):
            continue
        group_connection = _agent_connection_evidence(group, visible_agents)
        expected += int(group_connection.get("expected") or 0)
        connected += int(group_connection.get("connected") or 0)
        group_id = str(group.get("group_id") or "unknown")
        for item in _as_dict_list(group_connection.get("attention")):
            agent_id = str(item.get("agent_id") or "unknown")
            status = str(item.get("status") or "unknown")
            attention.append(f"{group_id}:{agent_id}:{status}")
    return {"expected": expected, "connected": connected, "attention": attention}


def _safe_process_meeting_id(value: object) -> str:
    meeting_id = clean_lobby_text(value, limit=128)
    if not meeting_id or meeting_id in {".", ".."}:
        return ""
    if "/" in meeting_id or "\\" in meeting_id or Path(meeting_id).name != meeting_id:
        return ""
    return meeting_id


def _groups_with_agent_connection_evidence(
    groups: list[dict[str, object]],
    agents: list[dict[str, object]],
) -> list[dict[str, object]]:
    return [{**group, "agent_connection": _agent_connection_evidence(group, agents)} for group in groups]


def _process_payload_with_agent_connection_evidence(
    payload: dict[str, object],
    output_root: Path | None,
) -> dict[str, object]:
    if output_root is None:
        return payload
    agents = read_live_agents(output_root)
    response = dict(payload)
    group = response.get("group")
    if isinstance(group, dict):
        response["group"] = {**group, "agent_connection": _agent_connection_evidence(group, agents)}
    groups = response.get("groups")
    if isinstance(groups, list):
        response["groups"] = _groups_with_agent_connection_evidence([group for group in groups if isinstance(group, dict)], agents)
    return response


def _agent_connection_evidence(group: dict[str, object], agents: list[dict[str, object]]) -> dict[str, object]:
    agents_by_id = {str(agent.get("agent_id") or ""): agent for agent in agents if str(agent.get("agent_id") or "")}
    expected = 0
    connected = 0
    attention = []
    for manifest_agent in _as_dict_list(group.get("agents")):
        agent_id = str(manifest_agent.get("agent_id") or "").strip()
        if not agent_id:
            continue
        expected += 1
        agent = agents_by_id.get(agent_id)
        if agent is None:
            attention.append({"agent_id": agent_id, "status": "missing"})
            continue
        status = str(agent.get("status") or "offline")
        if status in {"online", "working"}:
            connected += 1
            continue
        if status not in {"error", "stale", "offline"}:
            status = "offline"
        attention.append({"agent_id": agent_id, "status": status})
    return {"expected": expected, "connected": connected, "attention": attention}


def _diagnostic_agent_group_ids(agents: list[dict[str, object]]) -> set[str]:
    by_group: dict[str, set[str]] = {}
    for agent in agents:
        group_id, smoke_role = _smoke_agent_identity(agent)
        if group_id:
            by_group.setdefault(group_id, set()).add(smoke_role)
    return {group_id for group_id, roles in by_group.items() if {"local_cli", "live_session"}.issubset(roles)}


def _is_diagnostic_agent(agent: dict[str, object]) -> bool:
    return _payload_bool(agent.get("diagnostic")) or bool(_smoke_group_id_from_agent(agent))


def _is_diagnostic_process_group(group: dict[str, object], diagnostic_group_ids: set[str]) -> bool:
    return _payload_bool(group.get("diagnostic")) or _is_legacy_smoke_process_group(group, diagnostic_group_ids)


def _is_legacy_smoke_process_group(group: dict[str, object], diagnostic_group_ids: set[str]) -> bool:
    group_id = str(group.get("group_id") or "")
    if group_id not in diagnostic_group_ids:
        return False
    if str(group.get("status") or "") != "stopped":
        return False
    if group.get("returncode") not in (0, None):
        return False
    config_path = str(group.get("config_path") or "")
    if not config_path:
        return False
    return not Path(config_path).exists()


def _smoke_group_id_from_agent(agent: dict[str, object]) -> str:
    group_id, _ = _smoke_agent_identity(agent)
    return group_id


def _smoke_agent_identity(agent: dict[str, object]) -> tuple[str, str]:
    if str(agent.get("provider_kind") or "") != "local_cli":
        return "", ""
    agent_id = str(agent.get("agent_id") or "")
    display_name = str(agent.get("display_name") or "")
    connection_kind = str(agent.get("connection_kind") or "")
    if (
        display_name == "Smoke Local CLI"
        and connection_kind == "local_cli"
        and agent_id.endswith("-local-cli")
    ):
        return agent_id[: -len("-local-cli")], "local_cli"
    if (
        display_name == "Smoke Live Session"
        and connection_kind == "live_session"
        and agent_id.endswith("-live-session")
    ):
        return agent_id[: -len("-live-session")], "live_session"
    return "", ""


def start_live_agent_process_payload(
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
    output_root: Path | None = None,
) -> dict[str, object]:
    config_path = Path(str(payload.get("config_path") or "configs/live-agents.example.json"))
    server = str(payload.get("server") or default_server)
    group_id = str(payload.get("group_id") or "").strip() or None
    start_kwargs = {
        "config_path": config_path,
        "server": server,
        "group_id": group_id,
        "auto_restart": _payload_bool(payload.get("auto_restart")),
        "max_restarts": _payload_nonnegative_int(payload.get("max_restarts"), 0),
        "restart_backoff_seconds": _payload_nonnegative_float(payload.get("restart_backoff_seconds"), 5.0),
    }
    stale_restart_after_seconds = _payload_nonnegative_float(payload.get("stale_restart_after_seconds"), 0.0)
    if stale_restart_after_seconds > 0:
        start_kwargs["stale_restart_after_seconds"] = stale_restart_after_seconds
    if _payload_bool(payload.get("diagnostic")):
        start_kwargs["diagnostic"] = True
    group = process_supervisor.start_group(**start_kwargs)
    response = {"group": group, "groups": process_supervisor.list_groups()}
    return _process_payload_with_agent_connection_evidence(response, output_root)


def stop_live_agent_process_payload(
    process_supervisor: LiveAgentProcessSupervisor,
    group_id: str,
    *,
    output_root: Path | None = None,
) -> dict[str, object]:
    group = process_supervisor.stop_group(group_id)
    response = {"group": group, "groups": process_supervisor.list_groups()}
    return _process_payload_with_agent_connection_evidence(response, output_root)


def restart_live_agent_process_payload(
    process_supervisor: LiveAgentProcessSupervisor,
    group_id: str,
    *,
    output_root: Path | None = None,
) -> dict[str, object]:
    group = process_supervisor.restart_group(group_id)
    response = {"group": group, "groups": process_supervisor.list_groups()}
    return _process_payload_with_agent_connection_evidence(response, output_root)


def recover_live_agent_process_payload(
    process_supervisor: LiveAgentProcessSupervisor,
    group_id: str,
    *,
    output_root: Path | None = None,
) -> dict[str, object]:
    group = process_supervisor.recover_group(group_id)
    response = {"group": group, "groups": process_supervisor.list_groups()}
    return _process_payload_with_agent_connection_evidence(response, output_root)


def record_live_agent_operation(
    output_root: Path,
    *,
    operation: str,
    status: str,
    target_id: str = "",
    summary: str = "",
    error: str = "",
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    return append_live_agent_operation(
        output_root,
        operation=operation,
        status=status,
        target_id=target_id,
        summary=summary,
        error=error,
        details=details or {},
    )


def codex_session_invite_payload(
    output_root: Path,
    *,
    session_id: str,
    role_id: str,
    meeting_id: str | None = None,
) -> dict[str, object]:
    config_path = output_root / "codex-live-session.local.json"
    role_ids = _codex_invite_role_ids(output_root, meeting_id)
    config = build_codex_live_invite_config(
        session_id=session_id,
        role_id=role_id,
        role_ids=role_ids,
        existing=read_agent_config(config_path),
    )
    write_agent_config(config_path, config)
    binding = _binding_for_role(config.get("agent_bindings", []), role_id)
    return {"config_path": str(config_path), "binding": binding}


def _codex_invite_role_ids(output_root: Path, meeting_id: str | None) -> list[str]:
    if meeting_id:
        meeting_dir = output_root / "meetings" / meeting_id
        if not meeting_dir.exists():
            raise ValueError(f"Meeting {meeting_id} was not found.")
        meeting = _read_meeting_record(meeting_dir)
        role_ids = [str(role["id"]) for role in _as_dict_list(meeting.get("roles", [])) if role.get("id")]
        if role_ids:
            return role_ids
    return [role.id for role in load_council_config().roles]


def _binding_for_role(bindings: object, role_id: str) -> dict[str, object]:
    for binding in _as_dict_list(bindings):
        if binding.get("role_id") == role_id:
            return binding
    raise ValueError(f"No Codex live binding was written for role {role_id}.")


def _live_agent_for_id(output_root: Path, agent_id: str) -> dict[str, object]:
    for agent in read_live_agents(output_root):
        if agent.get("agent_id") == agent_id:
            return agent
    raise ValueError(f"Live agent {agent_id} was not found.")


def _safe_meeting_dir(output_root: Path, meeting_id: str) -> Path:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    if not clean_meeting_id or clean_meeting_id in {".", ".."}:
        raise ValueError(f"Meeting {clean_meeting_id or '(blank)'} was not found.")
    if "/" in clean_meeting_id or "\\" in clean_meeting_id or Path(clean_meeting_id).name != clean_meeting_id:
        raise ValueError(f"Meeting {clean_meeting_id} was not found.")
    meetings_root = (output_root / "meetings").resolve()
    meeting_dir = (meetings_root / clean_meeting_id).resolve()
    try:
        meeting_dir.relative_to(meetings_root)
    except ValueError as error:
        raise ValueError(f"Meeting {clean_meeting_id} was not found.") from error
    return meeting_dir


def _live_events_visible_to_agent(events: list[dict[str, object]], agent_id: str) -> list[dict[str, object]]:
    return [event for event in events if _live_event_visible_to_agent(event, agent_id)]


def _live_event_visible_to_agent(event: dict[str, object], agent_id: str) -> bool:
    if event.get("official_record") is True:
        return True
    target_agent_id = str(event.get("target_agent_id") or "")
    if target_agent_id:
        return target_agent_id == agent_id
    audience = str(event.get("audience") or "")
    if audience.startswith("agent:"):
        return audience == f"agent:{agent_id}"
    return True


def _matching_live_agent_turn_request(meeting_dir: Path, agent_id: str, source_event_id: str) -> dict[str, object] | None:
    for event in read_live_events(meeting_dir, limit=None):
        if event.get("id") != source_event_id:
            continue
        if event.get("kind") != "live_agent_turn_request":
            return None
        if str(event.get("target_agent_id") or "") != agent_id:
            return None
        return event
    return None


def _official_turn_reply_for_request(meeting_dir: Path, agent_id: str, source_event_id: str) -> dict[str, object] | None:
    for event in read_live_events(meeting_dir, limit=None):
        if event.get("kind") != "message":
            continue
        if str(event.get("actor_id") or "") != agent_id:
            continue
        if str(event.get("source_event_id") or "") != source_event_id:
            continue
        return event
    return None


def _live_agent_action_path(path: str, action: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[0] == "api" and parts[1] == "live-agents" and parts[3] == action:
        return unquote(parts[2])
    return None


def _meeting_live_agent_turn_request_path(path: str) -> str | None:
    return _meeting_live_agent_turn_action_path(path, "request")


def _meeting_live_agent_turn_call_path(path: str) -> str | None:
    return _meeting_live_agent_turn_action_path(path, "call")


def _meeting_live_agent_turn_sequence_path(path: str) -> str | None:
    return _meeting_live_agent_turn_action_path(path, "sequence")


def _meeting_live_agent_turn_rounds_path(path: str) -> str | None:
    return _meeting_live_agent_turn_action_path(path, "rounds")


def _meeting_live_agent_turn_round_path(path: str) -> str | None:
    return _meeting_live_agent_turn_action_path(path, "round")


def _meeting_live_agent_turn_action_path(path: str, action: str) -> str | None:
    parts = path.strip("/").split("/")
    if (
        len(parts) == 5
        and parts[0] == "api"
        and parts[1] == "meetings"
        and parts[3] == "live-agent-turns"
        and parts[4] == action
    ):
        return unquote(parts[2])
    return None


def _live_agent_process_action_path(path: str, action: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[0] == "api" and parts[1] == "live-agent-processes" and parts[3] == action:
        return unquote(parts[2])
    return None


def _resolve_lobby_meeting_dir(output_root: Path, meeting_id: str | None) -> Path:
    if meeting_id:
        meeting_dir = output_root / "meetings" / meeting_id
        if meeting_dir.exists():
            return meeting_dir
        raise ValueError(f"Meeting {meeting_id} was not found.")
    meetings = list_meetings(output_root)
    if not meetings:
        raise ValueError("No meeting is available for remote lobby chat.")
    return Path(str(meetings[0]["path"]))


def _read_meeting_record(meeting_dir: Path) -> dict[str, object]:
    meeting_path = meeting_dir / "meeting.json"
    live_path = meeting_dir / "live_state.json"
    if meeting_path.exists():
        meeting = json.loads(meeting_path.read_text(encoding="utf-8"))
        return _merge_live_progress_from_path(meeting, live_path)
    if live_path.exists():
        return json.loads(live_path.read_text(encoding="utf-8"))
    else:
        raise ValueError("Meeting record is missing.")


def _merge_live_progress_from_path(meeting: dict[str, object], live_path: Path) -> dict[str, object]:
    if not live_path.exists():
        return meeting
    try:
        live_state = json.loads(live_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return meeting
    if not isinstance(live_state, dict):
        return meeting
    return _merge_live_progress_into_meeting_record(meeting, live_state)


def _merge_live_progress_into_meeting_record(
    meeting: dict[str, object],
    live_state: dict[str, object],
) -> dict[str, object]:
    merged = dict(meeting)
    live_rounds = _as_dict_list(live_state.get("debate_rounds"))
    if live_rounds:
        merged["debate_rounds"] = _merge_debate_round_records(_as_dict_list(meeting.get("debate_rounds")), live_rounds)
    return merged


def _merge_debate_round_records(
    base_rounds: list[dict[str, object]],
    live_rounds: list[dict[str, object]],
) -> list[dict[str, object]]:
    merged_rounds = [dict(item) for item in base_rounds]
    indexes = {
        round_id: index
        for index, item in enumerate(merged_rounds)
        if (round_id := clean_lobby_text(item.get("id") or item.get("round"), limit=128))
    }
    for live_item in live_rounds:
        round_id = clean_lobby_text(live_item.get("id") or live_item.get("round"), limit=128)
        if not round_id:
            continue
        if round_id in indexes:
            index = indexes[round_id]
            base_item = merged_rounds[index]
            base_status = clean_lobby_text(base_item.get("status"), limit=32)
            live_status = clean_lobby_text(live_item.get("status"), limit=32)
            merged_item = dict(base_item)
            merged_item.update(live_item)
            if base_status == "answered" and live_status != "answered":
                merged_item["status"] = "answered"
            merged_rounds[index] = merged_item
        else:
            indexes[round_id] = len(merged_rounds)
            merged_rounds.append(dict(live_item))
    return merged_rounds


def _select_remote_bridge_binding(
    meeting: dict[str, object],
    target_agent_id: str | None,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    roles = _index_by_id(meeting.get("roles", []))
    providers = _index_by_id(meeting.get("provider_configs", []))
    for binding in _as_dict_list(meeting.get("agent_bindings", [])):
        if target_agent_id and binding.get("agent_id") != target_agent_id:
            continue
        provider = providers.get(str(binding.get("provider_id")))
        if not provider or provider.get("kind") != "remote_http_bridge":
            continue
        role = roles.get(str(binding.get("role_id")))
        if role:
            return role, binding, provider
    raise ValueError("No remote bridge lobby participant is available.")


def _index_by_id(items: object) -> dict[str, dict[str, object]]:
    return {str(item["id"]): item for item in _as_dict_list(items) if item.get("id")}


def _as_dict_list(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _role_from_payload(payload: dict[str, object]) -> Role:
    return Role(
        id=str(payload.get("id") or "remote"),
        display_name=str(payload.get("display_name") or payload.get("id") or "원격 에이전트"),
        lens=str(payload.get("lens") or "Remote participant"),
        research_focus=str(payload.get("research_focus") or "Lobby participation"),
        personality=payload.get("personality") if isinstance(payload.get("personality"), dict) else None,
        source_preferences=payload.get("source_preferences") if isinstance(payload.get("source_preferences"), list) else None,
    )


def _provider_from_payload(payload: dict[str, object]) -> ProviderConfig:
    return ProviderConfig(
        id=str(payload.get("id") or "remote"),
        kind="remote_http_bridge",
        display_name=str(payload.get("display_name") or payload.get("id") or "Remote bridge"),
        default_model=_optional_str(payload.get("default_model")),
        endpoint=_optional_str(payload.get("endpoint")),
        auth_ref=_optional_str(payload.get("auth_ref")),
        timeout_seconds=payload.get("timeout_seconds") if isinstance(payload.get("timeout_seconds"), int) else None,
        search_enabled=bool(payload.get("search_enabled")),
        notes=_optional_str(payload.get("notes")),
    )


def _runtime_provider_for_binding(
    meeting: dict[str, object],
    binding: dict[str, object],
    public_provider: dict[str, object],
) -> ProviderConfig:
    provider_id = str(binding.get("provider_id") or public_provider.get("id") or "remote")
    runtime_provider = _provider_from_agent_config(meeting.get("agent_config_source"), provider_id)
    if runtime_provider is not None:
        return runtime_provider
    auth_ref = _optional_str(public_provider.get("auth_ref"))
    if auth_ref == "literal:<redacted>" or auth_ref == "<redacted>":
        raise ValueError(
            "Remote bridge credential is not available from the public meeting artifact. "
            "Use an env: auth_ref or rerun with the original agent config available."
        )
    return _provider_from_payload(public_provider)


def _provider_from_agent_config(source: object, provider_id: str) -> ProviderConfig | None:
    if not isinstance(source, str) or not source or source == "default":
        return None
    config_path = Path(source)
    if not config_path.exists():
        return None
    runtime_config = load_agent_runtime_config(config_path)
    if runtime_config is None:
        return None
    return providers_from_config(runtime_config).get(provider_id)


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _operation_group_id(payload: dict[str, object], group: dict[str, object] | None = None) -> str:
    if group is not None and group.get("group_id"):
        return str(group["group_id"])
    return str(payload.get("group_id") or "").strip()


def _operation_agent_engagement(output_root: Path, agent_id: str) -> str:
    for agent in read_live_agents(output_root):
        if str(agent.get("agent_id") or "") == agent_id:
            return str(agent.get("engagement_mode") or "")
    return ""


def _operation_result_status(value: object) -> str:
    return str(value or "unknown").strip() or "unknown"


def _operation_success_for_result(value: object, *, success_values: set[str]) -> str:
    return "success" if _operation_result_status(value) in success_values else "failed"


def _payload_probe_agent_ids(value: object) -> list[str]:
    raw_items = value if isinstance(value, list) else []
    agent_ids = []
    seen = set()
    for item in raw_items:
        if not isinstance(item, str):
            continue
        agent_id = item.strip()[:64]
        if not agent_id or agent_id in seen:
            continue
        seen.add(agent_id)
        agent_ids.append(agent_id)
    return agent_ids


def _payload_probe_group_ids(value: object) -> list[str]:
    raw_items = value if isinstance(value, list) else []
    group_ids = []
    seen = set()
    for item in raw_items:
        if not isinstance(item, str):
            continue
        group_id = item.strip()[:64]
        if not group_id or group_id in seen:
            continue
        seen.add(group_id)
        group_ids.append(group_id)
    return group_ids


def _invalid_probe_id_payload(value: object) -> bool:
    if value is None:
        return False
    if not isinstance(value, list):
        return True
    return any(not isinstance(item, str) for item in value)


def _readiness_probe_plan(
    groups: list[dict[str, object]],
    *,
    requested_agent_ids: list[str],
    requested_group_ids: list[str],
) -> dict[str, list[dict[str, object]] | list[str]]:
    agent_ids = []
    seen_agents = set()
    for agent_id in requested_agent_ids:
        if agent_id not in seen_agents:
            seen_agents.add(agent_id)
            agent_ids.append(agent_id)

    groups_by_id = {str(group.get("group_id") or ""): group for group in groups}
    probe_groups: list[dict[str, object]] = []
    for group_id in requested_group_ids:
        group = groups_by_id.get(group_id)
        if group is None:
            probe_groups.append({"status": "failed", "group_id": group_id, "reason": "group was not found"})
            continue
        if str(group.get("status") or "") != "running":
            probe_groups.append({"status": "failed", "group_id": group_id, "reason": "group is not running"})
            continue
        manifest_agent_ids = _manifest_agent_ids(group.get("agents"))
        if not manifest_agent_ids:
            probe_groups.append({"status": "failed", "group_id": group_id, "reason": "group has no manifest agents"})
            continue
        probe_groups.append({"status": "ok", "group_id": group_id, "agent_ids": manifest_agent_ids})
        for agent_id in manifest_agent_ids:
            if agent_id in seen_agents:
                continue
            seen_agents.add(agent_id)
            agent_ids.append(agent_id)
    return {"agent_ids": agent_ids, "probe_groups": probe_groups}


def _manifest_agent_ids(value: object) -> list[str]:
    agent_ids = []
    seen = set()
    for item in _as_dict_list(value):
        agent_id = str(item.get("agent_id") or "").strip()[:64]
        if not agent_id or agent_id in seen:
            continue
        seen.add(agent_id)
        agent_ids.append(agent_id)
    return agent_ids


def _safe_readiness_smoke_result(smoke: dict[str, object]) -> dict[str, object]:
    safe = {
        "status": str(smoke.get("status") or "unknown"),
        "group_id": str(smoke.get("group_id") or ""),
    }
    agent_ids = _payload_probe_agent_ids(smoke.get("agent_ids"))
    if agent_ids:
        safe["agent_ids"] = agent_ids
    source_event_id = str(smoke.get("source_event_id") or "").strip()[:128]
    if source_event_id:
        safe["source_event_id"] = source_event_id
    replies = smoke.get("replies") if isinstance(smoke.get("replies"), list) else []
    safe["reply_count"] = len(replies)
    error = str(smoke.get("error") or "").strip()[:240]
    if error:
        safe["error"] = error
    return safe


def _safe_readiness_official_round_smoke_result(smoke: dict[str, object]) -> dict[str, object]:
    safe = {
        "status": str(smoke.get("status") or "unknown"),
        "group_id": clean_lobby_text(smoke.get("group_id"), limit=128),
        "meeting_id": clean_lobby_text(smoke.get("meeting_id"), limit=128),
        "round_id": clean_lobby_text(smoke.get("round_id"), limit=128),
        "agent_ids": _safe_payload_strings(smoke.get("agent_ids"), limit=64),
        "role_ids": _safe_payload_strings(smoke.get("role_ids"), limit=128),
        "turn_count": _payload_nonnegative_int(smoke.get("turn_count"), 0),
        "answered_count": _payload_nonnegative_int(smoke.get("answered_count"), 0),
        "timeout_count": _payload_nonnegative_int(smoke.get("timeout_count"), 0),
        "skipped_count": _payload_nonnegative_int(smoke.get("skipped_count"), 0),
        "stopped": smoke.get("stopped") is True,
        "timeout_seconds": _payload_nonnegative_float(smoke.get("timeout_seconds"), 0.0),
        "statuses": _safe_payload_strings(smoke.get("statuses"), limit=32),
        "request_event_ids": _safe_payload_strings(smoke.get("request_event_ids"), limit=128),
        "reply_event_ids": _safe_payload_strings(smoke.get("reply_event_ids"), limit=128),
    }
    error = str(smoke.get("error") or "").strip()[:240]
    if error:
        safe["error"] = OFFICIAL_ROUND_SMOKE_ERROR
    reason = str(smoke.get("reason") or "").strip()[:128]
    if reason:
        safe["reason"] = reason
    return safe


def _safe_readiness_probe_groups(
    probe_groups: list[dict[str, object]],
    *,
    include_agent_ids: bool,
) -> list[dict[str, object]]:
    safe_groups = []
    for group in probe_groups:
        safe_group = {
            "status": str(group.get("status") or "unknown"),
            "group_id": str(group.get("group_id") or ""),
        }
        agent_ids = _payload_probe_agent_ids(group.get("agent_ids"))
        if agent_ids and include_agent_ids:
            safe_group["agent_ids"] = agent_ids
        elif agent_ids:
            safe_group["agent_count"] = len(agent_ids)
        reason = str(group.get("reason") or "").strip()[:128]
        if reason:
            safe_group["reason"] = reason
        safe_groups.append(safe_group)
    return safe_groups


def _safe_readiness_probe_result(probe: dict[str, object]) -> dict[str, object]:
    safe = {
        "status": str(probe.get("status") or "unknown"),
        "agent_id": str(probe.get("agent_id") or ""),
    }
    for key in ("agent_status", "reason", "source_event_id", "reply_event_id"):
        value = str(probe.get(key) or "")
        if value:
            safe[key] = value[:128]
    return safe


def _probe_statuses(probes: object) -> list[str]:
    if not isinstance(probes, list):
        return []
    statuses = []
    for probe in probes:
        if not isinstance(probe, dict):
            continue
        agent_id = str(probe.get("agent_id") or "").strip()
        status = str(probe.get("status") or "unknown").strip() or "unknown"
        if agent_id:
            statuses.append(f"{agent_id}:{status}")
    return statuses


def _probe_group_statuses(probe_groups: object) -> list[str]:
    if not isinstance(probe_groups, list):
        return []
    statuses = []
    for group in probe_groups:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("group_id") or "").strip()
        status = str(group.get("status") or "unknown").strip() or "unknown"
        if group_id:
            statuses.append(f"{group_id}:{status}")
    return statuses


def _payload_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _payload_nonnegative_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (OverflowError, TypeError, ValueError):
        return default
    return max(0, parsed)


def _payload_nonnegative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(0.0, parsed)


def _payload_optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _payload_turn_sequence(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise ValueError("Official turn sequence requires a non-empty turns list.")
    if len(value) > MAX_LIVE_AGENT_SEQUENCE_TURNS:
        raise ValueError(f"Official turn sequence supports at most {MAX_LIVE_AGENT_SEQUENCE_TURNS} turns.")
    turns = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"Official turn sequence item {index} must be an object.")
        turns.append(dict(item))
    return turns


def _payload_turn_count(payload: dict[str, object]) -> int:
    turns = payload.get("turns")
    return len(turns) if isinstance(turns, list) else 0


def _payload_role_ids(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Official round role_ids must be an array.")
    role_ids = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"Official round role_ids item {index} must be a string.")
        role_ids.append(item)
    return role_ids


def _safe_payload_role_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    role_ids = []
    for item in value:
        if not isinstance(item, str):
            continue
        role_id = clean_lobby_text(item, limit=128)
        if role_id:
            role_ids.append(role_id)
    return role_ids


def _safe_payload_strings(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    strings = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = clean_lobby_text(item, limit=limit)
        if text:
            strings.append(text)
    return strings


def _validate_live_agent_turn_sequence(output_root: Path, meeting_id: str, turns: list[dict[str, object]]) -> str:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    meeting_dir = _safe_meeting_dir(output_root, clean_meeting_id)
    if not clean_meeting_id or not meeting_dir.exists():
        raise ValueError(f"Meeting {clean_meeting_id or '(blank)'} was not found.")
    for index, turn in enumerate(turns):
        agent_id = clean_lobby_text(turn.get("agent_id"), limit=64)
        if not agent_id:
            raise ValueError(f"Official turn sequence item {index} requires agent_id.")
        agent = _live_agent_for_id(output_root, agent_id)
        agent_meeting_id = str(agent.get("meeting_id") or "").strip()
        if agent_meeting_id != clean_meeting_id:
            raise ValueError(f"Live agent {agent_id} is not attached to meeting {clean_meeting_id}.")
        content = clean_lobby_text(turn.get("content") or turn.get("message"), limit=4000)
        if not content:
            raise ValueError(f"Official turn sequence item {index} requires content.")
    return clean_meeting_id


def _live_agent_turn_sequence_result(index: int, result: dict[str, object]) -> dict[str, object]:
    request_event = result.get("request_event") if isinstance(result.get("request_event"), dict) else {}
    reply_event = result.get("reply_event") if isinstance(result.get("reply_event"), dict) else None
    return {
        "index": index,
        "agent_id": str(request_event.get("target_agent_id") or ""),
        "role_id": str(request_event.get("role_id") or ""),
        "status": str(result.get("status") or "unknown"),
        "request_event": request_event,
        "reply_event": reply_event,
        "elapsed_seconds": _payload_nonnegative_float(result.get("elapsed_seconds"), 0.0),
        "timeout_seconds": _payload_nonnegative_float(result.get("timeout_seconds"), 0.0),
    }


def _skipped_turn_sequence_results(turns: list[dict[str, object]], *, start_index: int) -> list[dict[str, object]]:
    skipped = []
    for offset, turn in enumerate(turns):
        skipped.append(
            {
                "index": start_index + offset,
                "agent_id": clean_lobby_text(turn.get("agent_id"), limit=64),
                "role_id": clean_lobby_text(turn.get("role_id"), limit=128),
                "status": "skipped",
                "request_event": None,
                "reply_event": None,
                "elapsed_seconds": 0.0,
                "timeout_seconds": _payload_nonnegative_float(turn.get("timeout_seconds", turn.get("timeout")), 0.0),
            }
        )
    return skipped


def _live_agent_turn_sequence_status(answered_count: int, timeout_count: int, skipped_count: int) -> str:
    if timeout_count == 0 and skipped_count == 0:
        return "answered"
    if skipped_count:
        return "stopped"
    return "timeout"


def _turn_sequence_operation_details(sequence: dict[str, object], meeting_id: str) -> dict[str, object]:
    results = sequence.get("results") if isinstance(sequence.get("results"), list) else []
    request_event_ids = []
    reply_event_ids = []
    agent_ids = []
    statuses = []
    for item in results:
        if not isinstance(item, dict):
            continue
        request_event = item.get("request_event") if isinstance(item.get("request_event"), dict) else {}
        reply_event = item.get("reply_event") if isinstance(item.get("reply_event"), dict) else {}
        if request_event.get("id"):
            request_event_ids.append(str(request_event.get("id") or ""))
        if reply_event.get("id"):
            reply_event_ids.append(str(reply_event.get("id") or ""))
        if item.get("agent_id"):
            agent_ids.append(str(item.get("agent_id") or ""))
        if item.get("status"):
            statuses.append(str(item.get("status") or "unknown"))
    return {
        "meeting_id": meeting_id,
        "turn_count": _payload_nonnegative_int(sequence.get("turn_count"), 0),
        "answered_count": _payload_nonnegative_int(sequence.get("answered_count"), 0),
        "timeout_count": _payload_nonnegative_int(sequence.get("timeout_count"), 0),
        "skipped_count": _payload_nonnegative_int(sequence.get("skipped_count"), 0),
        "stopped": sequence.get("stopped") is True,
        "agent_ids": agent_ids,
        "statuses": statuses,
        "request_event_ids": request_event_ids,
        "reply_event_ids": reply_event_ids,
        "timeout_seconds": _payload_nonnegative_float(sequence.get("timeout_seconds"), 0.0),
    }


def _turn_round_operation_details(round_result: dict[str, object], meeting_id: str) -> dict[str, object]:
    details = _turn_sequence_operation_details(round_result, meeting_id)
    details["round_id"] = clean_lobby_text(round_result.get("round_id"), limit=128)
    details["role_ids"] = _safe_payload_role_ids(round_result.get("role_ids"))
    return details


def _turn_rounds_request_operation_details(payload: dict[str, object], meeting_id: str) -> dict[str, object]:
    return {
        "meeting_id": meeting_id,
        "timeout_seconds": _payload_nonnegative_float(payload.get("timeout_seconds", payload.get("timeout")), 30.0),
        "stop_on_timeout": _payload_bool(payload.get("stop_on_timeout")),
        "max_rounds": _payload_bounded_round_count(payload.get("max_rounds")),
    }


def _turn_rounds_operation_details(rounds_result: dict[str, object], meeting_id: str) -> dict[str, object]:
    results = rounds_result.get("results") if isinstance(rounds_result.get("results"), list) else []
    round_ids = []
    statuses = []
    role_ids = []
    for item in results:
        if not isinstance(item, dict):
            continue
        if item.get("round_id"):
            round_ids.append(clean_lobby_text(item.get("round_id"), limit=128))
        if item.get("status"):
            statuses.append(clean_lobby_text(item.get("status"), limit=32))
        role_ids.extend(_safe_payload_role_ids(item.get("role_ids")))
    return {
        "meeting_id": meeting_id,
        "round_count": _payload_nonnegative_int(rounds_result.get("round_count"), 0),
        "answered_round_count": _payload_nonnegative_int(rounds_result.get("answered_round_count"), 0),
        "completed_round_count": _payload_nonnegative_int(rounds_result.get("completed_round_count"), 0),
        "timeout_round_count": _payload_nonnegative_int(rounds_result.get("timeout_round_count"), 0),
        "skipped_round_count": _payload_nonnegative_int(rounds_result.get("skipped_round_count"), 0),
        "stopped_round_count": _payload_nonnegative_int(rounds_result.get("stopped_round_count"), 0),
        "stopped": rounds_result.get("stopped") is True,
        "round_ids": round_ids,
        "statuses": statuses,
        "role_ids": role_ids,
        "timeout_seconds": _payload_nonnegative_float(rounds_result.get("timeout_seconds"), 0.0),
        "max_rounds": _payload_nonnegative_int(rounds_result.get("max_rounds"), 0),
    }


def _official_round_smoke_operation_details(smoke: dict[str, object]) -> dict[str, object]:
    return {
        "group_id": clean_lobby_text(smoke.get("group_id"), limit=128),
        "result_status": _operation_result_status(smoke.get("status")),
        "meeting_id": clean_lobby_text(smoke.get("meeting_id"), limit=128),
        "round_id": clean_lobby_text(smoke.get("round_id"), limit=128),
        "agent_ids": _safe_payload_strings(smoke.get("agent_ids"), limit=64),
        "role_ids": _safe_payload_strings(smoke.get("role_ids"), limit=128),
        "turn_count": _payload_nonnegative_int(smoke.get("turn_count"), 0),
        "answered_count": _payload_nonnegative_int(smoke.get("answered_count"), 0),
        "timeout_count": _payload_nonnegative_int(smoke.get("timeout_count"), 0),
        "skipped_count": _payload_nonnegative_int(smoke.get("skipped_count"), 0),
        "stopped": smoke.get("stopped") is True,
        "timeout_seconds": _payload_nonnegative_float(smoke.get("timeout_seconds"), 0.0),
        "statuses": _safe_payload_strings(smoke.get("statuses"), limit=32),
        "request_event_ids": _safe_payload_strings(smoke.get("request_event_ids"), limit=128),
        "reply_event_ids": _safe_payload_strings(smoke.get("reply_event_ids"), limit=128),
    }


def _session_smoke_operation_details(smoke: dict[str, object]) -> dict[str, object]:
    return {
        "group_id": clean_lobby_text(smoke.get("group_id"), limit=128),
        "meeting_id": clean_lobby_text(smoke.get("meeting_id"), limit=128),
        "result_status": _operation_result_status(smoke.get("status")),
        "agent_ids": _safe_payload_strings(smoke.get("agent_ids"), limit=64),
        "rounds_status": _operation_result_status(smoke.get("rounds_status")),
        "round_count": _payload_nonnegative_int(smoke.get("round_count"), 0),
        "answered_round_count": _payload_nonnegative_int(smoke.get("answered_round_count"), 0),
        "completed_round_count": _payload_nonnegative_int(smoke.get("completed_round_count"), 0),
        "timeout_round_count": _payload_nonnegative_int(smoke.get("timeout_round_count"), 0),
        "skipped_round_count": _payload_nonnegative_int(smoke.get("skipped_round_count"), 0),
        "expected_reply_count": _payload_nonnegative_int(smoke.get("expected_reply_count"), 0),
        "reply_count": _payload_nonnegative_int(smoke.get("reply_count"), 0),
        "start_status": _operation_result_status(smoke.get("start_status")),
        "check_status": _operation_result_status(smoke.get("check_status")),
        "restart_status": _operation_result_status(smoke.get("restart_status")),
        "stop_status": _operation_result_status(smoke.get("stop_status")),
    }


def _session_smoke_error_details(payload: dict[str, object]) -> dict[str, object]:
    return {
        "group_id": clean_lobby_text(payload.get("group_id"), limit=128),
        "meeting_id": clean_lobby_text(payload.get("meeting_id"), limit=128),
    }


def _session_start_operation_details(session: dict[str, object]) -> dict[str, object]:
    connection = session.get("connection") if isinstance(session.get("connection"), dict) else {}
    process = session.get("process") if isinstance(session.get("process"), dict) else {}
    details = {
        "result_status": _operation_result_status(session.get("status")),
        "meeting_id": clean_lobby_text(session.get("meeting_id"), limit=128),
        "group_id": clean_lobby_text(session.get("group_id"), limit=128),
        "expected_agent_count": _payload_nonnegative_int(connection.get("expected"), 0),
        "connected_agent_count": _payload_nonnegative_int(connection.get("connected"), 0),
        "agent_ids": _safe_payload_strings(connection.get("agent_ids"), limit=64),
        "connected_agent_ids": _safe_payload_strings(connection.get("connected_agent_ids"), limit=64),
        "attention": _safe_payload_strings(connection.get("attention"), limit=128),
        "process_status": clean_lobby_text(process.get("status"), limit=64),
        "process_agent_ids": _safe_payload_strings(process.get("agent_ids"), limit=64),
        "process_attention": _safe_payload_strings(process.get("attention"), limit=128),
    }
    auto_rounds = session.get("auto_rounds") if isinstance(session.get("auto_rounds"), dict) else None
    if auto_rounds is not None:
        details.update(_session_auto_rounds_operation_details(auto_rounds, str(session.get("meeting_id") or "")))
    return details


def _session_stop_operation_details(session: dict[str, object]) -> dict[str, object]:
    offline = session.get("offline") if isinstance(session.get("offline"), dict) else {}
    process = session.get("process") if isinstance(session.get("process"), dict) else {}
    return {
        "result_status": _operation_result_status(session.get("status")),
        "meeting_id": clean_lobby_text(session.get("meeting_id"), limit=128),
        "group_id": clean_lobby_text(session.get("group_id"), limit=128),
        "expected_agent_count": _payload_nonnegative_int(offline.get("expected"), 0),
        "offline_agent_count": _payload_nonnegative_int(offline.get("offline"), 0),
        "agent_ids": _safe_payload_strings(offline.get("agent_ids"), limit=64),
        "offline_agent_ids": _safe_payload_strings(offline.get("offline_agent_ids"), limit=64),
        "attention": _safe_payload_strings(offline.get("attention"), limit=128),
        "process_status": clean_lobby_text(process.get("status"), limit=64),
        "process_agent_ids": _safe_payload_strings(process.get("agent_ids"), limit=64),
        "process_attention": _safe_payload_strings(process.get("attention"), limit=128),
    }


def _session_check_operation_details(session: dict[str, object]) -> dict[str, object]:
    return _session_start_operation_details(session)


def _session_auto_rounds_operation_details(auto_rounds: dict[str, object], meeting_id: str) -> dict[str, object]:
    rounds_details = _turn_rounds_operation_details(auto_rounds, meeting_id)
    return {
        "auto_rounds_status": _operation_result_status(auto_rounds.get("status")),
        "auto_rounds_reason": clean_lobby_text(auto_rounds.get("reason"), limit=128),
        "auto_rounds_meeting_id": rounds_details["meeting_id"],
        "auto_rounds_round_count": rounds_details["round_count"],
        "auto_rounds_answered_round_count": rounds_details["answered_round_count"],
        "auto_rounds_completed_round_count": rounds_details["completed_round_count"],
        "auto_rounds_timeout_round_count": rounds_details["timeout_round_count"],
        "auto_rounds_skipped_round_count": rounds_details["skipped_round_count"],
        "auto_rounds_stopped_round_count": rounds_details["stopped_round_count"],
        "auto_rounds_stopped": rounds_details["stopped"],
        "auto_rounds_round_ids": rounds_details["round_ids"],
        "auto_rounds_statuses": rounds_details["statuses"],
        "auto_rounds_role_ids": rounds_details["role_ids"],
        "auto_rounds_timeout_seconds": rounds_details["timeout_seconds"],
        "auto_rounds_max_rounds": rounds_details["max_rounds"],
    }


def _session_start_operation_status(session: dict[str, object]) -> str:
    if _operation_result_status(session.get("status")) != "ready":
        return "degraded"
    auto_rounds = session.get("auto_rounds") if isinstance(session.get("auto_rounds"), dict) else None
    if auto_rounds is None:
        return "success"
    return "success" if _operation_result_status(auto_rounds.get("status")) in {"answered", "complete"} else "degraded"


def _session_start_operation_summary(session: dict[str, object]) -> str:
    if _operation_result_status(session.get("status")) != "ready":
        return "resident live-agent session is still connecting"
    auto_rounds = session.get("auto_rounds") if isinstance(session.get("auto_rounds"), dict) else None
    if auto_rounds is None:
        return "started resident live-agent session"
    if _operation_result_status(auto_rounds.get("status")) in {"answered", "complete"}:
        return "started resident live-agent session and ran remaining rounds"
    return "started resident live-agent session with degraded remaining rounds"


def _session_resume_operation_summary(session: dict[str, object]) -> str:
    if _operation_result_status(session.get("status")) != "ready":
        return "resident live-agent session is still reconnecting"
    auto_rounds = session.get("auto_rounds") if isinstance(session.get("auto_rounds"), dict) else None
    if auto_rounds is None:
        return "resumed resident live-agent session"
    if _operation_result_status(auto_rounds.get("status")) in {"answered", "complete"}:
        return "resumed resident live-agent session and ran remaining rounds"
    return "resumed resident live-agent session with degraded remaining rounds"


def _session_stop_operation_status(session: dict[str, object]) -> str:
    return "success" if _operation_result_status(session.get("status")) == "stopped" else "degraded"


def _session_check_operation_status(session: dict[str, object]) -> str:
    return "success" if _operation_result_status(session.get("status")) == "ready" else "degraded"


def _session_stop_operation_summary(session: dict[str, object]) -> str:
    if _operation_result_status(session.get("status")) == "stopped":
        return "stopped resident live-agent session"
    return "resident live-agent session is still stopping"


def _session_check_operation_summary(session: dict[str, object]) -> str:
    if _operation_result_status(session.get("status")) == "ready":
        return "checked ready resident live-agent session"
    return "checked degraded resident live-agent session"


def _session_restart_operation_summary(session: dict[str, object]) -> str:
    if _operation_result_status(session.get("status")) == "ready":
        return "restarted resident live-agent session"
    return "resident live-agent session is still reconnecting after restart"


def _session_start_error_message(error: Exception) -> str:
    return _session_error_message(error, action="start")


def _session_resume_error_message(error: Exception) -> str:
    return _session_error_message(error, action="resume")


def _session_restart_error_message(error: Exception) -> str:
    return _session_error_message(error, action="restart")


def _session_check_error_message(error: Exception) -> str:
    return _session_error_message(error, action="check")


def _session_stop_error_message(error: Exception) -> str:
    return _session_error_message(error, action="stop")


def _session_error_message(error: Exception, *, action: str) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ").strip()
    fallback = f"Resident live-agent session {action} failed."
    if _looks_sensitive_session_error(message):
        return f"Resident live-agent session {action} failed: details redacted."
    return message[:500] or fallback


def _looks_sensitive_session_error(message: str) -> bool:
    lowered = message.casefold()
    return "/" in message or "\\" in message or ".json" in lowered or "command" in lowered


def _session_start_error_details(payload: dict[str, object], error: Exception) -> dict[str, object]:
    details = {"group_id": clean_lobby_text(payload.get("group_id"), limit=128)}
    recoverable_meeting_id = clean_lobby_text(getattr(error, "meeting_id", ""), limit=128)
    if recoverable_meeting_id:
        details["meeting_id"] = recoverable_meeting_id
        details["recoverable_meeting_id"] = recoverable_meeting_id
        return details
    requested_meeting_id = clean_lobby_text(payload.get("meeting_id"), limit=128)
    if requested_meeting_id:
        details["requested_meeting_id"] = requested_meeting_id
    return details


def _turn_round_request_operation_details(payload: dict[str, object], meeting_id: str) -> dict[str, object]:
    return {
        "meeting_id": meeting_id,
        "round_id": clean_lobby_text(payload.get("round_id"), limit=128),
        "role_ids": _safe_payload_role_ids(payload.get("role_ids")),
        "timeout_seconds": _payload_nonnegative_float(payload.get("timeout_seconds", payload.get("timeout")), 30.0),
        "stop_on_timeout": _payload_bool(payload.get("stop_on_timeout")),
    }


def _request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    timeout_seconds: float = 15.0,
) -> dict[str, object]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _local_server_url(server_address: tuple[object, ...]) -> str:
    host, port = server_address[:2]
    host = str(host)
    if host in {"", "0.0.0.0"}:
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"


def _make_handler(
    output_root: Path,
    *,
    process_supervisor: LiveAgentProcessSupervisor | None = None,
) -> type[BaseHTTPRequestHandler]:
    static_root = Path(__file__).parent / "static"
    live_agent_process_supervisor = process_supervisor or LiveAgentProcessSupervisor(output_root)

    class AgentsAssembleHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path == "/":
                self._send_file(static_root / "index.html", "text/html; charset=utf-8")
                return
            if path.startswith("/static/"):
                rel = path.removeprefix("/static/")
                static_path = _safe_static_path(static_root, rel)
                if static_path is None:
                    self._send_error(HTTPStatus.NOT_FOUND, "File not found")
                    return
                self._send_file(static_path)
                return
            if path == "/api/meetings":
                self._send_json({"meetings": list_meetings(output_root)})
                return
            if path == "/api/lobby":
                self._send_json({"events": read_lobby(output_root)})
                return
            if path == "/api/events/lobby":
                self._send_sse_stream("lobby", "lobby", last_event_id=self._last_event_id(query))
                return
            if path == "/api/side-chat":
                self._send_json({"events": read_side_chat(output_root)})
                return
            if path == "/api/events/side-chat":
                self._send_sse_stream("side_chat", "side_chat", last_event_id=self._last_event_id(query))
                return
            if path == "/api/providers":
                self._send_json(provider_catalog_payload())
                return
            if path == "/api/live-agents":
                self._send_json(live_agents_payload(output_root))
                return
            if path == "/api/live-agent-health":
                self._send_json(live_agent_health_payload(output_root, live_agent_process_supervisor))
                return
            if path == "/api/live-agent-processes":
                self._send_json(live_agent_processes_payload(live_agent_process_supervisor, output_root=output_root))
                return
            if path == "/api/live-agent-operations":
                self._send_json(live_agent_operations_payload(output_root, limit=self._limit(query, default=50)))
                return
            live_agent_room_id = _live_agent_action_path(path, "room")
            if live_agent_room_id is not None:
                try:
                    self._send_json(live_agent_room_payload(output_root, live_agent_room_id))
                except ValueError as error:
                    self._send_error(HTTPStatus.NOT_FOUND, str(error))
                return
            if path == "/api/codex-sessions":
                self._send_json(codex_sessions_payload(limit=self._limit(query, default=20)))
                return
            if path == "/api/meetings/latest":
                meetings = list_meetings(output_root)
                if not meetings:
                    self._send_json({"meeting": None})
                    return
                self._send_json(build_meeting_payload(Path(str(meetings[0]["path"]))))
                return
            meeting_events_id = self._meeting_events_id(path)
            if meeting_events_id:
                meeting_dir = output_root / "meetings" / meeting_events_id
                if not meeting_dir.exists():
                    self._send_error(HTTPStatus.NOT_FOUND, "Meeting not found")
                    return
                self._send_sse_stream("meeting", "meeting", meeting_id=meeting_events_id, last_event_id=self._last_event_id(query))
                return
            if path.startswith("/api/meetings/"):
                meeting_id = unquote(path.removeprefix("/api/meetings/"))
                meeting_dir = output_root / "meetings" / meeting_id
                if not meeting_dir.exists():
                    self._send_error(HTTPStatus.NOT_FOUND, "Meeting not found")
                    return
                self._send_json(build_meeting_payload(meeting_dir))
                return
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/demo":
                result = run_demo_meeting(adapter_name="mock", output_root=output_root)
                self._send_json({"meeting_id": result.meeting_id, "path": str(result.meeting_dir)})
                return
            if parsed.path == "/api/lobby":
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except json.JSONDecodeError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                event = append_lobby_event(output_root, payload if isinstance(payload, dict) else {})
                self._send_json({"event": event, "events": read_lobby(output_root)})
                return
            if parsed.path == "/api/side-chat":
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except json.JSONDecodeError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                event = append_side_chat_event(output_root, payload if isinstance(payload, dict) else {})
                self._send_json({"event": event, "events": read_side_chat(output_root)})
                return
            if parsed.path == "/api/lobby/remote":
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except json.JSONDecodeError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                if not isinstance(payload, dict):
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                try:
                    event = send_lobby_message_to_remote_bridge(
                        output_root,
                        str(payload.get("message") or ""),
                        meeting_id=_optional_str(payload.get("meeting_id")),
                        target_agent_id=_optional_str(payload.get("target_agent_id")),
                        speaker_name=str(payload.get("speaker_name") or "나"),
                    )
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json({"event": event, "events": read_lobby(output_root)})
                return
            if parsed.path == "/api/live-agent-sessions/start":
                payload = self._operation_json_payload(operation="session.start")
                if payload is None:
                    return
                try:
                    session = live_agent_session_start_payload(
                        output_root,
                        live_agent_process_supervisor,
                        payload,
                        default_server=self._request_server_url(),
                    )
                except (OSError, ValueError) as error:
                    safe_error = _session_start_error_message(error)
                    safe_details = _session_start_error_details(payload, error)
                    record_live_agent_operation(
                        output_root,
                        operation="session.start",
                        status="failed",
                        target_id=str(safe_details.get("meeting_id") or ""),
                        error=safe_error,
                        details=safe_details,
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, safe_error, details=safe_details)
                    return
                record_live_agent_operation(
                    output_root,
                    operation="session.start",
                    status=_session_start_operation_status(session),
                    target_id=str(session.get("meeting_id") or payload.get("meeting_id") or ""),
                    summary=_session_start_operation_summary(session),
                    details=_session_start_operation_details(session),
                )
                self._send_json(session)
                return
            if parsed.path == "/api/live-agent-sessions/resume":
                payload = self._operation_json_payload(operation="session.resume")
                if payload is None:
                    return
                try:
                    session = live_agent_session_resume_payload(
                        output_root,
                        live_agent_process_supervisor,
                        payload,
                        default_server=self._request_server_url(),
                    )
                except (OSError, ValueError) as error:
                    safe_error = _session_resume_error_message(error)
                    safe_details = _session_start_error_details(payload, error)
                    record_live_agent_operation(
                        output_root,
                        operation="session.resume",
                        status="failed",
                        target_id=str(safe_details.get("meeting_id") or safe_details.get("requested_meeting_id") or ""),
                        error=safe_error,
                        details=safe_details,
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, safe_error, details=safe_details)
                    return
                record_live_agent_operation(
                    output_root,
                    operation="session.resume",
                    status=_session_start_operation_status(session),
                    target_id=str(session.get("meeting_id") or payload.get("meeting_id") or ""),
                    summary=_session_resume_operation_summary(session),
                    details=_session_start_operation_details(session),
                )
                self._send_json(session)
                return
            if parsed.path == "/api/live-agent-sessions/check":
                payload = self._operation_json_payload(operation="session.check")
                if payload is None:
                    return
                try:
                    session = live_agent_session_check_payload(
                        output_root,
                        live_agent_process_supervisor,
                        payload,
                    )
                except (OSError, ValueError) as error:
                    safe_error = _session_check_error_message(error)
                    safe_details = _session_start_error_details(payload, error)
                    record_live_agent_operation(
                        output_root,
                        operation="session.check",
                        status="failed",
                        target_id=str(safe_details.get("meeting_id") or safe_details.get("requested_meeting_id") or ""),
                        error=safe_error,
                        details=safe_details,
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, safe_error, details=safe_details)
                    return
                record_live_agent_operation(
                    output_root,
                    operation="session.check",
                    status=_session_check_operation_status(session),
                    target_id=str(session.get("meeting_id") or payload.get("meeting_id") or ""),
                    summary=_session_check_operation_summary(session),
                    details=_session_check_operation_details(session),
                )
                self._send_json(session)
                return
            if parsed.path == "/api/live-agent-sessions/restart":
                payload = self._operation_json_payload(operation="session.restart")
                if payload is None:
                    return
                try:
                    session = live_agent_session_restart_payload(
                        output_root,
                        live_agent_process_supervisor,
                        payload,
                    )
                except (OSError, ValueError) as error:
                    safe_error = _session_restart_error_message(error)
                    safe_details = _session_start_error_details(payload, error)
                    record_live_agent_operation(
                        output_root,
                        operation="session.restart",
                        status="failed",
                        target_id=str(safe_details.get("meeting_id") or safe_details.get("requested_meeting_id") or ""),
                        error=safe_error,
                        details=safe_details,
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, safe_error, details=safe_details)
                    return
                record_live_agent_operation(
                    output_root,
                    operation="session.restart",
                    status=_session_start_operation_status(session),
                    target_id=str(session.get("meeting_id") or payload.get("meeting_id") or ""),
                    summary=_session_restart_operation_summary(session),
                    details=_session_start_operation_details(session),
                )
                self._send_json(session)
                return
            if parsed.path == "/api/live-agent-sessions/stop":
                payload = self._operation_json_payload(operation="session.stop")
                if payload is None:
                    return
                try:
                    session = live_agent_session_stop_payload(
                        output_root,
                        live_agent_process_supervisor,
                        payload,
                    )
                except (OSError, ValueError) as error:
                    safe_error = _session_stop_error_message(error)
                    safe_details = _session_start_error_details(payload, error)
                    record_live_agent_operation(
                        output_root,
                        operation="session.stop",
                        status="failed",
                        target_id=str(safe_details.get("meeting_id") or safe_details.get("requested_meeting_id") or ""),
                        error=safe_error,
                        details=safe_details,
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, safe_error, details=safe_details)
                    return
                record_live_agent_operation(
                    output_root,
                    operation="session.stop",
                    status=_session_stop_operation_status(session),
                    target_id=str(session.get("meeting_id") or payload.get("meeting_id") or ""),
                    summary=_session_stop_operation_summary(session),
                    details=_session_stop_operation_details(session),
                )
                self._send_json(session)
                return
            if parsed.path == "/api/live-agent-meetings/start":
                payload = self._operation_json_payload(operation="meeting.start")
                if payload is None:
                    return
                try:
                    started = live_agent_meeting_start_payload(output_root, payload)
                except (OSError, ValueError) as error:
                    record_live_agent_operation(
                        output_root,
                        operation="meeting.start",
                        status="failed",
                        target_id=str(payload.get("meeting_id") or ""),
                        error=str(error),
                        details={"meeting_id": str(payload.get("meeting_id") or "")},
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                meeting = started.get("meeting") if isinstance(started.get("meeting"), dict) else {}
                record_live_agent_operation(
                    output_root,
                    operation="meeting.start",
                    status="success",
                    target_id=str(started.get("meeting_id") or payload.get("meeting_id") or ""),
                    summary="started resident live-agent meeting",
                    details={
                        "meeting_id": str(started.get("meeting_id") or ""),
                        "role_count": len(meeting.get("roles") if isinstance(meeting.get("roles"), list) else []),
                        "bound_agent_count": len(
                            meeting.get("agent_bindings") if isinstance(meeting.get("agent_bindings"), list) else []
                        ),
                    },
                )
                self._send_json(started)
                return
            if parsed.path == "/api/live-agents":
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except json.JSONDecodeError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                if not isinstance(payload, dict):
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                try:
                    live_agent = connect_live_agent_payload(output_root, payload)
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json(live_agent)
                return
            turn_rounds_meeting_id = _meeting_live_agent_turn_rounds_path(parsed.path)
            if turn_rounds_meeting_id is not None:
                payload = self._operation_json_payload(operation="official_turn.rounds")
                if payload is None:
                    return
                try:
                    rounds_result = live_agent_turn_rounds_payload(output_root, turn_rounds_meeting_id, payload)
                except ValueError as error:
                    record_live_agent_operation(
                        output_root,
                        operation="official_turn.rounds",
                        status="failed",
                        target_id=turn_rounds_meeting_id,
                        error=str(error),
                        details=_turn_rounds_request_operation_details(payload, turn_rounds_meeting_id),
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                record_live_agent_operation(
                    output_root,
                    operation="official_turn.rounds",
                    status="success" if rounds_result.get("status") in {"answered", "complete"} else "degraded",
                    target_id=turn_rounds_meeting_id,
                    summary=(
                        "completed live-agent remaining official rounds"
                        if rounds_result.get("status") in {"answered", "complete"}
                        else "live-agent remaining official rounds had timeouts"
                    ),
                    details=_turn_rounds_operation_details(rounds_result, turn_rounds_meeting_id),
                )
                self._send_json(rounds_result)
                return
            turn_round_meeting_id = _meeting_live_agent_turn_round_path(parsed.path)
            if turn_round_meeting_id is not None:
                payload = self._operation_json_payload(operation="official_turn.round")
                if payload is None:
                    return
                try:
                    round_result = live_agent_turn_round_payload(output_root, turn_round_meeting_id, payload)
                except ValueError as error:
                    record_live_agent_operation(
                        output_root,
                        operation="official_turn.round",
                        status="failed",
                        target_id=turn_round_meeting_id,
                        error=str(error),
                        details=_turn_round_request_operation_details(payload, turn_round_meeting_id),
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                record_live_agent_operation(
                    output_root,
                    operation="official_turn.round",
                    status="success" if round_result.get("status") in {"answered", "complete"} else "degraded",
                    target_id=turn_round_meeting_id,
                    summary=(
                        "completed live-agent official round"
                        if round_result.get("status") in {"answered", "complete"}
                        else "live-agent official round had timeouts"
                    ),
                    details=_turn_round_operation_details(round_result, turn_round_meeting_id),
                )
                self._send_json(round_result)
                return
            turn_sequence_meeting_id = _meeting_live_agent_turn_sequence_path(parsed.path)
            if turn_sequence_meeting_id is not None:
                payload = self._operation_json_payload(operation="official_turn.sequence")
                if payload is None:
                    return
                try:
                    sequence = live_agent_turn_sequence_payload(output_root, turn_sequence_meeting_id, payload)
                except ValueError as error:
                    record_live_agent_operation(
                        output_root,
                        operation="official_turn.sequence",
                        status="failed",
                        target_id=turn_sequence_meeting_id,
                        error=str(error),
                        details={
                            "meeting_id": turn_sequence_meeting_id,
                            "turn_count": _payload_turn_count(payload),
                            "timeout_seconds": _payload_nonnegative_float(payload.get("timeout_seconds", payload.get("timeout")), 30.0),
                            "stop_on_timeout": _payload_bool(payload.get("stop_on_timeout")),
                        },
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                record_live_agent_operation(
                    output_root,
                    operation="official_turn.sequence",
                    status="success" if sequence.get("status") == "answered" else "degraded",
                    target_id=turn_sequence_meeting_id,
                    summary=(
                        "completed live-agent official turn sequence"
                        if sequence.get("status") == "answered"
                        else "live-agent official turn sequence had timeouts"
                    ),
                    details=_turn_sequence_operation_details(sequence, turn_sequence_meeting_id),
                )
                self._send_json(sequence)
                return
            turn_call_meeting_id = _meeting_live_agent_turn_call_path(parsed.path)
            if turn_call_meeting_id is not None:
                payload = self._operation_json_payload(operation="official_turn.call")
                if payload is None:
                    return
                target_agent_id = str(payload.get("agent_id") or "").strip()
                try:
                    turn_call = live_agent_turn_call_payload(output_root, turn_call_meeting_id, payload)
                except ValueError as error:
                    record_live_agent_operation(
                        output_root,
                        operation="official_turn.call",
                        status="failed",
                        target_id=target_agent_id,
                        error=str(error),
                        details={
                            "meeting_id": turn_call_meeting_id,
                            "target_agent_id": target_agent_id,
                            "role_id": str(payload.get("role_id") or ""),
                            "turn_id": str(payload.get("turn_id") or ""),
                            "turn_index": _payload_optional_int(payload.get("turn_index")),
                            "timeout_seconds": _payload_nonnegative_float(payload.get("timeout_seconds", payload.get("timeout")), 30.0),
                        },
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                request_event = turn_call.get("request_event") if isinstance(turn_call.get("request_event"), dict) else {}
                reply_event = turn_call.get("reply_event") if isinstance(turn_call.get("reply_event"), dict) else {}
                result_status = str(turn_call.get("status") or "unknown")
                record_live_agent_operation(
                    output_root,
                    operation="official_turn.call",
                    status="success" if result_status == "answered" else "degraded",
                    target_id=str(request_event.get("target_agent_id") or target_agent_id),
                    summary=(
                        "completed live-agent official turn"
                        if result_status == "answered"
                        else "timed out waiting for live-agent official turn"
                    ),
                    details={
                        "meeting_id": turn_call_meeting_id,
                        "target_agent_id": str(request_event.get("target_agent_id") or target_agent_id),
                        "role_id": str(request_event.get("role_id") or ""),
                        "turn_id": str(request_event.get("turn_id") or ""),
                        "turn_index": _payload_optional_int(request_event.get("turn_index")),
                        "source_event_id": str(request_event.get("id") or ""),
                        "reply_event_id": str(reply_event.get("id") or ""),
                        "timeout_seconds": _payload_nonnegative_float(turn_call.get("timeout_seconds"), 30.0),
                        "elapsed_seconds": _payload_nonnegative_float(turn_call.get("elapsed_seconds"), 0.0),
                    },
                )
                self._send_json(turn_call)
                return
            turn_request_meeting_id = _meeting_live_agent_turn_request_path(parsed.path)
            if turn_request_meeting_id is not None:
                payload = self._operation_json_payload(operation="official_turn.request")
                if payload is None:
                    return
                target_agent_id = str(payload.get("agent_id") or "").strip()
                try:
                    turn_request = live_agent_turn_request_payload(output_root, turn_request_meeting_id, payload)
                except ValueError as error:
                    record_live_agent_operation(
                        output_root,
                        operation="official_turn.request",
                        status="failed",
                        target_id=target_agent_id,
                        error=str(error),
                        details={
                            "meeting_id": turn_request_meeting_id,
                            "target_agent_id": target_agent_id,
                            "role_id": str(payload.get("role_id") or ""),
                            "turn_id": str(payload.get("turn_id") or ""),
                            "turn_index": _payload_optional_int(payload.get("turn_index")),
                        },
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                event = turn_request.get("event") if isinstance(turn_request.get("event"), dict) else {}
                record_live_agent_operation(
                    output_root,
                    operation="official_turn.request",
                    status="success",
                    target_id=str(event.get("target_agent_id") or target_agent_id),
                    summary="requested live-agent official turn",
                    details={
                        "meeting_id": turn_request_meeting_id,
                        "target_agent_id": str(event.get("target_agent_id") or target_agent_id),
                        "role_id": str(event.get("role_id") or ""),
                        "turn_id": str(event.get("turn_id") or ""),
                        "turn_index": _payload_optional_int(event.get("turn_index")),
                        "source_event_id": str(event.get("id") or ""),
                    },
                )
                self._send_json(turn_request)
                return
            live_agent_engagement_id = _live_agent_action_path(parsed.path, "engagement")
            if live_agent_engagement_id is not None:
                payload = self._operation_json_payload(
                    operation="engagement.update",
                    target_id=live_agent_engagement_id,
                )
                if payload is None:
                    return
                previous_mode = _operation_agent_engagement(output_root, live_agent_engagement_id)
                try:
                    engagement = update_live_agent_engagement_payload(output_root, live_agent_engagement_id, payload)
                except ValueError as error:
                    record_live_agent_operation(
                        output_root,
                        operation="engagement.update",
                        status="failed",
                        target_id=live_agent_engagement_id,
                        error=str(error),
                        details={"engagement_mode": str(payload.get("engagement_mode") or "")},
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                agent = engagement.get("agent") if isinstance(engagement.get("agent"), dict) else {}
                record_live_agent_operation(
                    output_root,
                    operation="engagement.update",
                    status="success",
                    target_id=live_agent_engagement_id,
                    summary="updated engagement mode",
                    details={
                        "previous_engagement_mode": previous_mode,
                        "engagement_mode": str(agent.get("engagement_mode") or payload.get("engagement_mode") or ""),
                    },
                )
                self._send_json(engagement)
                return
            if parsed.path == "/api/live-agent-processes/start":
                payload = self._operation_json_payload(operation="process.start")
                if payload is None:
                    return
                try:
                    started = start_live_agent_process_payload(
                        live_agent_process_supervisor,
                        payload,
                        default_server=self._request_server_url(),
                        output_root=output_root,
                    )
                except ValueError as error:
                    record_live_agent_operation(
                        output_root,
                        operation="process.start",
                        status="failed",
                        target_id=_operation_group_id(payload),
                        error=str(error),
                        details={
                            "group_id": _operation_group_id(payload),
                            "auto_restart": _payload_bool(payload.get("auto_restart")),
                            "max_restarts": _payload_nonnegative_int(payload.get("max_restarts"), 0),
                            "restart_backoff_seconds": _payload_nonnegative_float(
                                payload.get("restart_backoff_seconds"),
                                5.0,
                            ),
                            "stale_restart_after_seconds": _payload_nonnegative_float(
                                payload.get("stale_restart_after_seconds"),
                                0.0,
                            ),
                        },
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                group = started.get("group") if isinstance(started.get("group"), dict) else {}
                record_live_agent_operation(
                    output_root,
                    operation="process.start",
                    status="success",
                    target_id=_operation_group_id(payload, group),
                    summary="started live-agent process group",
                    details={
                        "group_id": _operation_group_id(payload, group),
                        "group_status": str(group.get("status") or ""),
                        "auto_restart": _payload_bool(payload.get("auto_restart")),
                        "max_restarts": _payload_nonnegative_int(payload.get("max_restarts"), 0),
                        "restart_backoff_seconds": _payload_nonnegative_float(
                            payload.get("restart_backoff_seconds"),
                            5.0,
                        ),
                        "stale_restart_after_seconds": _payload_nonnegative_float(
                            payload.get("stale_restart_after_seconds"),
                            0.0,
                        ),
                    },
                )
                self._send_json(started)
                return
            if parsed.path == "/api/live-agent-preflight":
                payload = self._operation_json_payload(operation="preflight.check")
                if payload is None:
                    return
                try:
                    preflight = live_agent_preflight_payload(payload, default_server=self._request_server_url())
                except ValueError as error:
                    record_live_agent_operation(
                        output_root,
                        operation="preflight.check",
                        status="failed",
                        error=str(error),
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                result_status = _operation_result_status(preflight.get("status"))
                record_live_agent_operation(
                    output_root,
                    operation="preflight.check",
                    status=_operation_success_for_result(result_status, success_values={"ok"}),
                    target_id=str(payload.get("group_id") or ""),
                    summary="checked live-agent config",
                    details={
                        "result_status": result_status,
                        "agents": (preflight.get("summary") or {}).get("agents", 0)
                        if isinstance(preflight.get("summary"), dict)
                        else 0,
                        "failed_agents": (preflight.get("summary") or {}).get("failed_agents", 0)
                        if isinstance(preflight.get("summary"), dict)
                        else 0,
                    },
                )
                self._send_json(preflight)
                return
            if parsed.path == "/api/provider-health":
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except json.JSONDecodeError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                if not isinstance(payload, dict):
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                try:
                    self._send_json(provider_health_payload(payload))
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                return
            if parsed.path == "/api/live-agent-smoke":
                payload = self._operation_json_payload(operation="smoke.run")
                if payload is None:
                    return
                try:
                    smoke = live_agent_smoke_payload(payload, default_server=self._local_server_url())
                except LiveAgentSmokeFailed as error:
                    record_live_agent_operation(
                        output_root,
                        operation="smoke.run",
                        status="failed",
                        target_id=str(payload.get("group_id") or ""),
                        error=str(error),
                        details={"group_id": str(payload.get("group_id") or "")},
                    )
                    self._send_error(HTTPStatus.CONFLICT, str(error))
                    return
                except (ValueError, urllib.error.URLError) as error:
                    record_live_agent_operation(
                        output_root,
                        operation="smoke.run",
                        status="failed",
                        target_id=str(payload.get("group_id") or ""),
                        error=str(error),
                        details={"group_id": str(payload.get("group_id") or "")},
                    )
                    self._send_error(HTTPStatus.BAD_GATEWAY, str(error))
                    return
                result_status = _operation_result_status(smoke.get("status"))
                record_live_agent_operation(
                    output_root,
                    operation="smoke.run",
                    status=_operation_success_for_result(result_status, success_values={"ok"}),
                    target_id=str(smoke.get("group_id") or payload.get("group_id") or ""),
                    summary="ran credential-free live-agent smoke",
                    details={"group_id": str(smoke.get("group_id") or ""), "result_status": result_status},
                )
                self._send_json(smoke)
                return
            if parsed.path == "/api/live-agent-official-round-smoke":
                payload = self._operation_json_payload(operation="smoke.official_round")
                if payload is None:
                    return
                try:
                    smoke = live_agent_official_round_smoke_payload(
                        output_root,
                        payload,
                        default_server=self._local_server_url(),
                    )
                except (LiveAgentSmokeFailed, ValueError, urllib.error.URLError) as error:
                    record_live_agent_operation(
                        output_root,
                        operation="smoke.official_round",
                        status="failed",
                        target_id=str(payload.get("group_id") or ""),
                        error=str(error),
                        details={"group_id": str(payload.get("group_id") or "")},
                    )
                    self._send_error(HTTPStatus.BAD_GATEWAY, str(error))
                    return
                result_status = _operation_result_status(smoke.get("status"))
                record_live_agent_operation(
                    output_root,
                    operation="smoke.official_round",
                    status=_operation_success_for_result(result_status, success_values={"ok"}),
                    target_id=str(smoke.get("group_id") or payload.get("group_id") or ""),
                    summary="ran credential-free official round smoke",
                    details=_official_round_smoke_operation_details(smoke),
                )
                self._send_json(smoke)
                return
            if parsed.path == "/api/live-agent-session-smoke":
                payload = self._operation_json_payload(operation="session.smoke")
                if payload is None:
                    return
                try:
                    smoke = live_agent_session_smoke_payload(
                        output_root,
                        payload,
                        default_server=self._local_server_url(),
                    )
                except (LiveAgentSmokeFailed, ValueError, urllib.error.URLError) as error:
                    del error
                    safe_error = "Session smoke could not be run."
                    safe_details = _session_smoke_error_details(payload)
                    record_live_agent_operation(
                        output_root,
                        operation="session.smoke",
                        status="failed",
                        target_id=str(safe_details.get("group_id") or ""),
                        error=safe_error,
                        details=safe_details,
                    )
                    self._send_error(HTTPStatus.BAD_GATEWAY, safe_error, details=safe_details)
                    return
                result_status = _operation_result_status(smoke.get("status"))
                record_live_agent_operation(
                    output_root,
                    operation="session.smoke",
                    status=_operation_success_for_result(result_status, success_values={"ok"}),
                    target_id=str(smoke.get("group_id") or payload.get("group_id") or ""),
                    summary="ran credential-free resident session smoke",
                    details=_session_smoke_operation_details(smoke),
                )
                self._send_json(smoke)
                return
            if parsed.path == "/api/live-agent-readiness":
                payload = self._operation_json_payload(operation="readiness.check")
                if payload is None:
                    return
                try:
                    readiness = live_agent_readiness_payload(
                        output_root,
                        live_agent_process_supervisor,
                        payload,
                        default_server=self._local_server_url(),
                    )
                except (ValueError, urllib.error.URLError) as error:
                    record_live_agent_operation(
                        output_root,
                        operation="readiness.check",
                        status="failed",
                        target_id=str(payload.get("group_id") or ""),
                        error=str(error),
                        details={"group_id": str(payload.get("group_id") or "")},
                    )
                    self._send_error(HTTPStatus.BAD_GATEWAY, str(error))
                    return
                result_status = _operation_result_status(readiness.get("status"))
                smoke = readiness.get("smoke") if isinstance(readiness.get("smoke"), dict) else {}
                official_round_smoke = (
                    readiness.get("official_round_smoke")
                    if isinstance(readiness.get("official_round_smoke"), dict)
                    else {}
                )
                probes = readiness.get("probes") if isinstance(readiness.get("probes"), list) else []
                probe_groups = readiness.get("probe_groups") if isinstance(readiness.get("probe_groups"), list) else []
                record_live_agent_operation(
                    output_root,
                    operation="readiness.check",
                    status="degraded"
                    if result_status == "degraded"
                    else _operation_success_for_result(result_status, success_values={"ready"}),
                    target_id=str(smoke.get("group_id") or payload.get("group_id") or ""),
                    summary="checked live-agent readiness",
                    details={
                        "group_id": str(smoke.get("group_id") or payload.get("group_id") or ""),
                        "result_status": result_status,
                        "probe_agent_ids": _payload_probe_agent_ids(payload.get("probe_agent_ids")),
                        "probe_group_ids": _payload_probe_group_ids(payload.get("probe_group_ids")),
                        "effective_probe_agent_ids": _payload_probe_agent_ids(readiness.get("effective_probe_agent_ids")),
                        "probe_error": str(readiness.get("probe_error") or ""),
                        "probe_group_statuses": _probe_group_statuses(probe_groups),
                        "probe_statuses": _probe_statuses(probes),
                        "official_round_smoke": _operation_result_status(official_round_smoke.get("status")),
                        "official_round_answered_count": _payload_nonnegative_int(
                            official_round_smoke.get("answered_count"),
                            0,
                        ),
                        "official_round_timeout_count": _payload_nonnegative_int(
                            official_round_smoke.get("timeout_count"),
                            0,
                        ),
                        "official_round_skipped_count": _payload_nonnegative_int(
                            official_round_smoke.get("skipped_count"),
                            0,
                        ),
                    },
                )
                self._send_json(readiness)
                return
            live_agent_process_stop_id = _live_agent_process_action_path(parsed.path, "stop")
            if live_agent_process_stop_id is not None:
                try:
                    stopped = stop_live_agent_process_payload(
                        live_agent_process_supervisor,
                        live_agent_process_stop_id,
                        output_root=output_root,
                    )
                except ValueError as error:
                    record_live_agent_operation(
                        output_root,
                        operation="process.stop",
                        status="failed",
                        target_id=live_agent_process_stop_id,
                        error=str(error),
                        details={"group_id": live_agent_process_stop_id},
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                group = stopped.get("group") if isinstance(stopped.get("group"), dict) else {}
                record_live_agent_operation(
                    output_root,
                    operation="process.stop",
                    status="success",
                    target_id=_operation_group_id({}, group) or live_agent_process_stop_id,
                    summary="stopped live-agent process group",
                    details={
                        "group_id": _operation_group_id({}, group) or live_agent_process_stop_id,
                        "group_status": str(group.get("status") or ""),
                    },
                )
                self._send_json(stopped)
                return
            live_agent_process_restart_id = _live_agent_process_action_path(parsed.path, "restart")
            if live_agent_process_restart_id is not None:
                try:
                    restarted = restart_live_agent_process_payload(
                        live_agent_process_supervisor,
                        live_agent_process_restart_id,
                        output_root=output_root,
                    )
                except ValueError as error:
                    record_live_agent_operation(
                        output_root,
                        operation="process.restart",
                        status="failed",
                        target_id=live_agent_process_restart_id,
                        error=str(error),
                        details={"group_id": live_agent_process_restart_id},
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                group = restarted.get("group") if isinstance(restarted.get("group"), dict) else {}
                record_live_agent_operation(
                    output_root,
                    operation="process.restart",
                    status="success",
                    target_id=_operation_group_id({}, group) or live_agent_process_restart_id,
                    summary="restarted live-agent process group",
                    details={
                        "group_id": _operation_group_id({}, group) or live_agent_process_restart_id,
                        "group_status": str(group.get("status") or ""),
                    },
                )
                self._send_json(restarted)
                return
            live_agent_process_recover_id = _live_agent_process_action_path(parsed.path, "recover")
            if live_agent_process_recover_id is not None:
                try:
                    recovered = recover_live_agent_process_payload(
                        live_agent_process_supervisor,
                        live_agent_process_recover_id,
                        output_root=output_root,
                    )
                except ValueError as error:
                    record_live_agent_operation(
                        output_root,
                        operation="process.recover",
                        status="failed",
                        target_id=live_agent_process_recover_id,
                        error=str(error),
                        details={"group_id": live_agent_process_recover_id},
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                group = recovered.get("group") if isinstance(recovered.get("group"), dict) else {}
                record_live_agent_operation(
                    output_root,
                    operation="process.recover",
                    status="success",
                    target_id=_operation_group_id({}, group) or live_agent_process_recover_id,
                    summary="recovered live-agent process group",
                    details={
                        "group_id": _operation_group_id({}, group) or live_agent_process_recover_id,
                        "group_status": str(group.get("status") or ""),
                        "previous_status": str(group.get("recovered_from_status") or ""),
                    },
                )
                self._send_json(recovered)
                return
            live_agent_probe_id = _live_agent_action_path(parsed.path, "probe")
            if live_agent_probe_id is not None:
                payload = self._operation_json_payload(operation="probe.run", target_id=live_agent_probe_id)
                if payload is None:
                    return
                timeout_seconds = safe_probe_timeout(
                    _payload_nonnegative_float(payload.get("timeout_seconds", payload.get("timeout")), 12.0)
                )
                try:
                    probe = live_agent_probe_payload(output_root, live_agent_probe_id, payload)
                except ValueError as error:
                    record_live_agent_operation(
                        output_root,
                        operation="probe.run",
                        status="failed",
                        target_id=live_agent_probe_id,
                        error=str(error),
                        details={"result_status": "failed", "timeout_seconds": timeout_seconds},
                    )
                    status = HTTPStatus.NOT_FOUND if "was not found" in str(error) else HTTPStatus.BAD_REQUEST
                    self._send_error(status, str(error))
                    return
                result_status = _operation_result_status(probe.get("status"))
                record_live_agent_operation(
                    output_root,
                    operation="probe.run",
                    status=_operation_success_for_result(result_status, success_values={"ok"}),
                    target_id=live_agent_probe_id,
                    summary="ran live-agent reply probe",
                    details={
                        "result_status": result_status,
                        "timeout_seconds": timeout_seconds,
                        "source_event_id": str(probe.get("source_event_id") or ""),
                        "reply_event_id": str(probe.get("reply_event_id") or ""),
                    },
                )
                self._send_json(probe)
                return
            live_agent_official_turn_id = _live_agent_action_path(parsed.path, "official-turn")
            if live_agent_official_turn_id is not None:
                payload = self._operation_json_payload(
                    operation="official_turn.reply",
                    target_id=live_agent_official_turn_id,
                )
                if payload is None:
                    return
                try:
                    official_turn = live_agent_official_turn_payload(output_root, live_agent_official_turn_id, payload)
                except ValueError as error:
                    record_live_agent_operation(
                        output_root,
                        operation="official_turn.reply",
                        status="failed",
                        target_id=live_agent_official_turn_id,
                        error=str(error),
                        details={
                            "meeting_id": str(payload.get("meeting_id") or ""),
                            "source_event_id": str(payload.get("source_event_id") or ""),
                            "role_id": str(payload.get("role_id") or ""),
                            "turn_id": str(payload.get("turn_id") or ""),
                            "turn_index": _payload_optional_int(payload.get("turn_index")),
                        },
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                event = official_turn.get("event") if isinstance(official_turn.get("event"), dict) else {}
                record_live_agent_operation(
                    output_root,
                    operation="official_turn.reply",
                    status="success",
                    target_id=live_agent_official_turn_id,
                    summary="recorded live-agent official turn",
                    details={
                        "meeting_id": str(event.get("meeting_id") or payload.get("meeting_id") or ""),
                        "source_event_id": str(event.get("source_event_id") or ""),
                        "role_id": str(event.get("role_id") or ""),
                        "turn_id": str(event.get("turn_id") or ""),
                        "turn_index": _payload_optional_int(event.get("turn_index")),
                    },
                )
                self._send_json(official_turn)
                return
            live_agent_heartbeat_id = _live_agent_action_path(parsed.path, "heartbeat")
            if live_agent_heartbeat_id is not None:
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except json.JSONDecodeError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                if not isinstance(payload, dict):
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                try:
                    heartbeat = live_agent_heartbeat_payload(output_root, live_agent_heartbeat_id, payload)
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json(heartbeat)
                return
            live_agent_lobby_id = _live_agent_action_path(parsed.path, "lobby")
            if live_agent_lobby_id is not None:
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except json.JSONDecodeError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                if not isinstance(payload, dict):
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                try:
                    message = live_agent_lobby_message_payload(output_root, live_agent_lobby_id, payload)
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json(message)
                return
            if parsed.path == "/api/codex-sessions/invite":
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except json.JSONDecodeError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                if not isinstance(payload, dict):
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                try:
                    invite = codex_session_invite_payload(
                        output_root,
                        session_id=str(payload.get("session_id") or ""),
                        role_id=str(payload.get("role_id") or ""),
                        meeting_id=_optional_str(payload.get("meeting_id")),
                    )
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json(invite)
                return
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

        def log_message(self, format: str, *args: object) -> None:
            return

        def _request_server_url(self) -> str:
            host = self.headers.get("Host")
            if host:
                return f"http://{host}"
            address = self.server.server_address
            return f"http://{address[0]}:{address[1]}"

        def _local_server_url(self) -> str:
            return _local_server_url(self.server.server_address)

        def _send_file(self, path: Path, content_type: str | None = None) -> None:
            if not path.exists() or not path.is_file():
                self._send_error(HTTPStatus.NOT_FOUND, "File not found")
                return
            guessed = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            data = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", guessed)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, payload: dict[str, object]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_sse_snapshot(self, event_name: str, payload: dict[str, object]) -> None:
            data = _sse_event(event_name, payload, event_id=_last_payload_event_id(payload))
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def _send_sse_stream(
            self,
            event_name: str,
            stream: str,
            meeting_id: str | None = None,
            last_event_id: str | None = None,
        ) -> None:
            self.close_connection = True
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            current_last_event_id = last_event_id
            current_payload_signature: str | None = None
            while True:
                try:
                    payload = _stream_snapshot_payload(
                        output_root,
                        stream,
                        meeting_id=meeting_id,
                        last_event_id=current_last_event_id,
                    )
                    latest_event_id = _last_payload_event_id(payload)
                    if latest_event_id:
                        self.wfile.write(_sse_event(event_name, payload, event_id=latest_event_id))
                        current_last_event_id = latest_event_id
                        current_payload_signature = _payload_signature(payload)
                    elif _payload_signature(payload) and _payload_signature(payload) != current_payload_signature:
                        self.wfile.write(_sse_event(event_name, payload))
                        current_payload_signature = _payload_signature(payload)
                    else:
                        self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    time.sleep(1)
                except (ValueError, FileNotFoundError) as error:
                    error_payload = _sse_stream_error_payload(stream, error, meeting_id=meeting_id)
                    try:
                        self.wfile.write(_sse_event("error", error_payload))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    return
                except (BrokenPipeError, ConnectionResetError):
                    return

        def _operation_json_payload(
            self,
            *,
            operation: str,
            target_id: str = "",
            details: dict[str, object] | None = None,
        ) -> dict[str, object] | None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                record_live_agent_operation(
                    output_root,
                    operation=operation,
                    status="failed",
                    target_id=target_id,
                    error="Invalid JSON",
                    details=details or {},
                )
                self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                return None
            if not isinstance(payload, dict):
                record_live_agent_operation(
                    output_root,
                    operation=operation,
                    status="failed",
                    target_id=target_id,
                    error="Invalid JSON",
                    details=details or {},
                )
                self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                return None
            return payload

        def _send_error(
            self,
            status: HTTPStatus,
            message: str,
            *,
            details: dict[str, object] | None = None,
        ) -> None:
            payload: dict[str, object] = {"error": message}
            if details:
                payload["details"] = details
                meeting_id = details.get("meeting_id")
                if meeting_id:
                    payload["meeting_id"] = meeting_id
                group_id = details.get("group_id")
                if group_id:
                    payload["group_id"] = group_id
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _last_event_id(self, query: dict[str, list[str]]) -> str | None:
            query_value = query.get("last_event_id", [None])[0]
            header_value = self.headers.get("Last-Event-ID")
            return _optional_str(header_value) or _optional_str(query_value)

        def _meeting_events_id(self, path: str) -> str | None:
            prefix = "/api/meetings/"
            suffix = "/events"
            if not path.startswith(prefix) or not path.endswith(suffix):
                return None
            meeting_id = path[len(prefix) : -len(suffix)]
            return unquote(meeting_id) if meeting_id else None

        def _limit(self, query: dict[str, list[str]], default: int) -> int:
            try:
                return int(query.get("limit", [str(default)])[0])
            except (TypeError, ValueError):
                return default

    return AgentsAssembleHandler


def _last_payload_event_id(payload: dict[str, object]) -> str | None:
    events = payload.get("events")
    if not isinstance(events, list) or not events:
        return None
    latest = events[-1]
    if not isinstance(latest, dict):
        return None
    event_id = latest.get("id")
    return event_id if isinstance(event_id, str) and event_id else None


def _payload_signature(payload: dict[str, object]) -> str | None:
    signature = payload.get("payload_signature")
    return signature if isinstance(signature, str) and signature else None


def _safe_static_path(static_root: Path, relative_path: str) -> Path | None:
    root = static_root.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate
