from __future__ import annotations

import json
import math
import mimetypes
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

from agentsassemble.adapters.remote_bridge import RemoteBridgeAdapter
from agentsassemble.codex_sessions import (
    CODEX_LIVE_PROVIDER_ID,
    DEFAULT_LIVE_AGENT_CONFIG_PATH,
    build_codex_live_agent_config,
    build_codex_live_invite_config,
    list_codex_sessions,
    read_agent_config,
    write_agent_config,
)
from agentsassemble.config import load_agent_runtime_config, load_council_config, providers_from_config
from agentsassemble.live_agent_discovery import (
    add_session_bundle_outputs,
    build_discovered_live_agent_config,
    build_discovered_session_bundle,
    discovered_session_bundle_paths,
    fill_discovery_next_command_output,
    validate_distinct_session_bundle_paths,
)
from agentsassemble.live_agent_preflight import preflight_live_agent_config
from agentsassemble.live_agent_runner import load_group_configs
from agentsassemble.live_agents import connect_live_agent, heartbeat_live_agent, read_live_agents, update_live_agent_engagement
from agentsassemble.live_agent_operations import append_live_agent_operation, read_live_agent_operation_history
from agentsassemble.live_agent_meetings import start_live_agent_meeting
from agentsassemble.live_agent_finalization import finalize_live_agent_meeting
from agentsassemble.live_agent_processes import (
    LiveAgentProcessSupervisor,
    clean_live_agent_group_id,
    read_live_agent_process_event_history,
)
from agentsassemble.live_agent_probe import PROBE_REPLY_EVENT_TAIL_LIMIT, run_live_agent_probe, safe_probe_timeout
from agentsassemble.live_agent_rounds import build_official_round_turns, completed_official_round_ids, remaining_official_round_ids
from agentsassemble.live_agent_sessions import (
    check_live_agent_session,
    live_agent_session_readiness_summary,
    recover_live_agent_session,
    restart_live_agent_session,
    resume_live_agent_session,
    session_ensure_action,
    start_live_agent_session,
    stop_live_agent_session,
)
from agentsassemble.live_agent_session_runs import LiveAgentSessionRunController
from agentsassemble.live_agent_smoke import (
    LiveAgentSmokeFailed,
    MAX_SESSION_SMOKE_SOAK_CYCLES,
    MAX_SESSION_SMOKE_SOAK_INTERVAL_SECONDS,
    run_live_agent_official_round_smoke,
    run_live_agent_session_smoke,
    run_live_agent_smoke,
)
from agentsassemble.live_agent_turns import (
    is_official_turn_reply_event,
    is_review_checkpoint_reply_event,
    wait_for_official_turn_reply,
    wait_for_review_checkpoint_reply,
)
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
LIVE_AGENT_ROOM_LOBBY_EVENT_LIMIT = PROBE_REPLY_EVENT_TAIL_LIMIT
SSE_ERROR_MESSAGE_LIMIT = 500
REMOTE_LOBBY_REQUESTER = None
MAX_READINESS_PROBE_AGENTS = 10
OFFICIAL_ROUND_SMOKE_ERROR = "official round smoke could not be run"
SESSION_SMOKE_ERROR = "session smoke could not be run"
LIVE_AGENT_TURN_LOCK = threading.Lock()
LIVE_AGENT_LOBBY_LOCK = threading.Lock()
MAX_LIVE_AGENT_SEQUENCE_TURNS = 12
MAX_LIVE_AGENT_ROUND_BATCH = 8
LIVE_AGENT_ROUND_SCHEDULER_LOCKS: dict[str, threading.RLock] = {}
LIVE_AGENT_ROUND_SCHEDULER_LOCKS_LOCK = threading.Lock()
DEFAULT_SESSION_RUN_MONITOR_INTERVAL_SECONDS = 30.0
MIN_SESSION_RUN_MONITOR_INTERVAL_SECONDS = 1.0
SESSION_RUN_MONITOR_ERROR = "Live-agent session run monitor failed."
HEALTH_WATCHDOG_REASON_EVENT_TYPES = {"stale_watchdog", "stale_watchdog_stop_failed"}
HEALTH_RESTART_FAILED_REASON_EVENT_TYPE = "restart_failed"
HEALTH_RECOVERED_UNKNOWN_REASON_EVENT_TYPE = "recovered_unknown"
HEALTH_RECOVERED_UNKNOWN_REASON = "orphan running record marked unknown"
SAFE_HEALTH_WATCHDOG_REASON_PATTERN = re.compile(
    r"^(?:(?:missing|stale|offline|error) manifest agent|wrong meeting manifest agent) [A-Za-z0-9_.-]{1,64}$"
)
SAFE_HEALTH_RESTART_FAILED_GROUP_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SAFE_HEALTH_RESTART_FAILED_ERROR_PATTERN = re.compile(
    r"Restart failed: Live agent group ([A-Za-z0-9_.-]{1,64}) has no (config|server) to (?:restart|recover)\."
)


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
    session_run_controller = LiveAgentSessionRunController(root)
    handler = _make_handler(
        root,
        process_supervisor=process_supervisor,
        session_run_controller=session_run_controller,
    )
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
        session_run_monitor = LiveAgentSessionRunMonitor(
            root,
            process_supervisor,
            session_run_controller,
            default_server=server_url,
        )
        session_run_monitor.start()
        print(f"AgentsAssemble GUI: {server_url}")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AgentsAssemble GUI")
    finally:
        if "session_run_monitor" in locals():
            session_run_monitor.stop()
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


def _reconcile_live_agent_session_runs_on_startup(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    session_run_controller: LiveAgentSessionRunController,
    *,
    default_server: str,
) -> list[dict[str, object]]:
    return _reconcile_live_agent_session_runs(
        output_root,
        process_supervisor,
        session_run_controller,
        default_server=default_server,
        summary="reconciled durable live-agent session runs on GUI startup",
    )


def _reconcile_live_agent_session_runs(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    session_run_controller: LiveAgentSessionRunController,
    *,
    default_server: str,
    summary: str,
    target_run_id: str = "",
) -> list[dict[str, object]]:
    def ensure_from_run(run: dict[str, object]) -> dict[str, object]:
        request = run.get("request") if isinstance(run.get("request"), dict) else {}
        return live_agent_session_ensure_payload(
            output_root,
            process_supervisor,
            dict(request),
            default_server=default_server,
        )

    results = session_run_controller.reconcile_active_runs(
        ensure_from_run,
        should_reconcile=lambda run: _session_run_monitor_should_reconcile(
            output_root,
            process_supervisor,
            run,
            target_run_id=target_run_id,
        ),
    )
    if results:
        failed_count = sum(1 for item in results if str(item.get("status") or "") == "failed")
        degraded_count = sum(
            1
            for item in results
            if str(item.get("status") or "") in {"running", "recovering", "starting", "degraded"}
        )
        status = "failed" if failed_count else "degraded" if degraded_count else "success"
        record_live_agent_operation(
            output_root,
            operation="session_run.reconcile",
            status=status,
            summary=summary,
            details={
                "session_run_count": len(results),
                "session_run_failed_count": failed_count,
                "session_run_degraded_count": degraded_count,
            },
        )
    return results


def _session_run_monitor_should_reconcile(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    run: dict[str, object],
    *,
    target_run_id: str = "",
) -> bool:
    if target_run_id and str(run.get("run_id") or "") != target_run_id:
        return False
    if _operation_result_status(run.get("status")) != "ready":
        return True
    meeting_id = str(run.get("meeting_id") or "").strip()
    group_id = str(run.get("group_id") or "").strip()
    if not meeting_id or not group_id:
        return True
    try:
        readiness = live_agent_session_readiness_payload(
            output_root,
            process_supervisor,
            meeting_id=meeting_id,
            group_id=group_id,
        )
    except (OSError, ValueError):
        return True
    return _operation_result_status(readiness.get("status")) != "ready"


class LiveAgentSessionRunMonitor:
    def __init__(
        self,
        output_root: Path,
        process_supervisor: LiveAgentProcessSupervisor,
        session_run_controller: LiveAgentSessionRunController,
        *,
        default_server: str,
        interval_seconds: float = DEFAULT_SESSION_RUN_MONITOR_INTERVAL_SECONDS,
    ) -> None:
        self.output_root = output_root
        self.process_supervisor = process_supervisor
        self.session_run_controller = session_run_controller
        self.default_server = default_server
        self.interval_seconds = _session_run_monitor_interval(interval_seconds)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event = threading.Event()
            thread = threading.Thread(
                target=self._loop,
                args=(self._stop_event,),
                daemon=True,
                name="AgentsAssembleLiveAgentSessionRunMonitor",
            )
            self._thread = thread
            thread.start()

    def stop(self, *, timeout_seconds: float | None = None) -> bool:
        with self._lock:
            stop_event = self._stop_event
            thread = self._thread
            self._thread = None
        stop_event.set()
        if thread is not None:
            if timeout_seconds is None:
                thread.join()
            else:
                thread.join(timeout=max(0.0, timeout_seconds))
            return not thread.is_alive()
        return True

    def run_once(self) -> list[dict[str, object]]:
        return _reconcile_live_agent_session_runs(
            self.output_root,
            self.process_supervisor,
            self.session_run_controller,
            default_server=self.default_server,
            summary="reconciled durable live-agent session runs during GUI runtime",
        )

    def _loop(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                self.run_once()
            except Exception as error:
                self._record_failure(error)
            if stop_event.wait(self.interval_seconds):
                break

    def _record_failure(self, error: Exception) -> None:
        record_live_agent_operation(
            self.output_root,
            operation="session_run.monitor",
            status="failed",
            summary="live-agent session-run monitor failed",
            error=SESSION_RUN_MONITOR_ERROR,
            details={"error_type": type(error).__name__},
        )


def _session_run_monitor_interval(value: object) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_SESSION_RUN_MONITOR_INTERVAL_SECONDS
    if not math.isfinite(seconds):
        return DEFAULT_SESSION_RUN_MONITOR_INTERVAL_SECONDS
    return max(MIN_SESSION_RUN_MONITOR_INTERVAL_SECONDS, seconds)


def _read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def read_lobby(output_root: Path, limit: int | None = 80) -> list[dict[str, object]]:
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


def live_agent_operations_payload(
    output_root: Path,
    *,
    limit: int = 50,
    operation: str = "",
    target_id: str = "",
    status: str = "",
    scan_limit: object = None,
    scan_tail: bool = False,
) -> dict[str, object]:
    return read_live_agent_operation_history(
        output_root,
        limit=limit,
        operation=operation,
        target_id=target_id,
        status=status,
        scan_limit=scan_limit,
        scan_tail=scan_tail,
    )


def live_agent_session_runs_payload(
    session_run_controller: LiveAgentSessionRunController,
    *,
    limit: int = 50,
    meeting_id: str = "",
    group_id: str = "",
    include_readiness: bool = False,
    output_root: Path | None = None,
    process_supervisor: LiveAgentProcessSupervisor | None = None,
) -> dict[str, object]:
    runs = session_run_controller.list_runs(limit=limit, meeting_id=meeting_id, group_id=group_id)
    if include_readiness and output_root is not None and process_supervisor is not None:
        runs = _session_runs_with_readiness(runs, output_root=output_root, process_supervisor=process_supervisor)
    return {"runs": runs}


def _session_runs_with_readiness(
    runs: list[dict[str, object]],
    *,
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
) -> list[dict[str, object]]:
    groups = _session_process_groups_snapshot(process_supervisor)
    summary = live_agent_session_readiness_summary(output_root, groups)
    items = summary.get("items") if isinstance(summary.get("items"), list) else []
    readiness_by_target = {
        (str(item.get("meeting_id") or ""), str(item.get("group_id") or "")): item
        for item in items
        if isinstance(item, dict)
    }
    return [
        {
            **run,
            "readiness": _session_run_readiness_overlay(run, readiness_by_target),
        }
        for run in runs
    ]


def _session_run_readiness_overlay(
    run: dict[str, object],
    readiness_by_target: dict[tuple[str, str], dict[str, object]],
) -> dict[str, object]:
    meeting_id = str(run.get("meeting_id") or "").strip()
    group_id = str(run.get("group_id") or "").strip()
    if not meeting_id or not group_id:
        return {"status": "degraded", "attention": ["session_run:missing_target"]}
    readiness = readiness_by_target.get((meeting_id, group_id))
    if readiness is None:
        return {
            "meeting_id": meeting_id,
            "group_id": group_id,
            "status": "degraded",
            "attention": ["session_run:no_current_readiness"],
        }
    return dict(readiness)


def live_agent_process_events_payload(
    output_root: Path,
    *,
    limit: int = 50,
    group_id: str = "",
    scan_limit: object = None,
) -> dict[str, object]:
    return read_live_agent_process_event_history(output_root, limit=limit, group_id=group_id, scan_limit=scan_limit)


def live_agent_meeting_start_payload(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    council_config_path = str(payload.get("council_config_path") or payload.get("council_config") or "").strip()
    agent_config_path = str(payload.get("agent_config_path") or payload.get("agent_config") or "").strip()
    return start_live_agent_meeting(
        output_root,
        council_config_path=Path(council_config_path) if council_config_path else None,
        agent_config_path=Path(agent_config_path) if agent_config_path else None,
        meeting_id=str(payload.get("meeting_id") or ""),
    )


def live_agent_finalize_meeting_payload(output_root: Path, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
    meeting_dir = _safe_meeting_dir(output_root, meeting_id)
    return finalize_live_agent_meeting(meeting_dir, force=_payload_bool(payload.get("force")))


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
    return _attach_session_auto_rounds_if_requested(output_root, session, payload)


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
    return _attach_session_auto_rounds_if_requested(output_root, session, payload)


def live_agent_session_ensure_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    payload = _live_agent_session_payload_with_group_owner(process_supervisor, payload)
    current = _live_agent_session_optional_readiness_payload(output_root, process_supervisor, payload)
    action = session_ensure_action(current)
    if action == "none" and _ready_session_requires_restart_for_resident_session_drift(
        output_root,
        process_supervisor,
        payload,
        current,
        default_server=default_server,
    ):
        action = "restart"
    if action == "none":
        session = _attach_session_auto_rounds_if_requested(output_root, dict(current) if isinstance(current, dict) else {}, payload)
    elif action == "start":
        session = live_agent_session_start_payload(
            output_root,
            process_supervisor,
            payload,
            default_server=default_server,
        )
    elif action == "restart":
        session = live_agent_session_restart_payload(output_root, process_supervisor, payload)
    elif action == "recover":
        session = live_agent_session_recover_payload(output_root, process_supervisor, payload)
    else:
        session = live_agent_session_resume_payload(
            output_root,
            process_supervisor,
            payload,
            default_server=default_server,
        )
    ensured = _live_agent_session_ensured_readiness_payload(output_root, process_supervisor, payload, session)
    ensured["action"] = action
    return ensured


def _live_agent_session_payload_with_group_owner(
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
) -> dict[str, object]:
    if str(payload.get("meeting_id") or "").strip():
        return payload
    group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        return payload
    group = _find_session_process_group(_session_process_groups_snapshot(process_supervisor), group_id)
    owned_meeting_id = _safe_process_group_meeting_id(group.get("meeting_id") if group else "")
    if not owned_meeting_id:
        return payload
    resolved = payload
    resolved["meeting_id"] = owned_meeting_id
    resolved["_meeting_id_resolved_from_group"] = True
    return resolved


def _safe_process_group_meeting_id(value: object) -> str:
    meeting_id = clean_lobby_text(value, limit=128)
    if not meeting_id or meeting_id in {".", ".."}:
        return ""
    if "/" in meeting_id or "\\" in meeting_id or Path(meeting_id).name != meeting_id:
        return ""
    return meeting_id


def _ready_session_requires_restart_for_resident_session_drift(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    current: dict[str, object] | None,
    *,
    default_server: str,
) -> bool:
    if not isinstance(current, dict) or _operation_result_status(current.get("status")) != "ready":
        return False
    live_agent_config_path = str(payload.get("live_agent_config_path") or payload.get("live_agent_config") or "").strip()
    if not live_agent_config_path:
        return False
    group_id = str(current.get("group_id") or payload.get("group_id") or "").strip()
    if not group_id:
        return False
    group = _find_session_process_group(_session_process_groups_snapshot(process_supervisor), group_id)
    if str(group.get("status") or "") not in {"running", "restarting"}:
        return False
    if not _process_group_uses_requested_config(group, live_agent_config_path):
        return False
    meeting_id = str(current.get("meeting_id") or payload.get("meeting_id") or "").strip()
    requested_session_ids = _resident_session_ids_by_agent(
        live_agent_config_path,
        server=str(payload.get("server") or default_server),
        meeting_id=meeting_id,
    )
    if not requested_session_ids:
        return False
    agents_by_id = {str(agent.get("agent_id") or ""): agent for agent in read_live_agents(output_root)}
    for agent_id, requested_session_id in requested_session_ids.items():
        current_agent = agents_by_id.get(agent_id)
        if not current_agent:
            continue
        if str(current_agent.get("meeting_id") or "").strip() != meeting_id:
            continue
        if str(current_agent.get("session_id") or "").strip() != requested_session_id:
            return True
    return False


def _process_group_uses_requested_config(group: dict[str, object], live_agent_config_path: str) -> bool:
    persisted_config_path = str(group.get("config_path") or "").strip()
    if not persisted_config_path:
        return False
    return Path(persisted_config_path).resolve(strict=False) == Path(live_agent_config_path).resolve(strict=False)


def _resident_session_ids_by_agent(
    live_agent_config_path: str,
    *,
    server: str,
    meeting_id: str,
) -> dict[str, str]:
    configs = load_group_configs(Path(live_agent_config_path), server_override=server)
    result: dict[str, str] = {}
    for config in configs:
        config_meeting_id = str(getattr(config, "meeting_id", "") or "").strip()
        if config_meeting_id and meeting_id and config_meeting_id != meeting_id:
            continue
        agent_id = str(getattr(config, "agent_id", "") or "").strip()
        session_id = str(getattr(config, "session_id", "") or "").strip()
        if agent_id and session_id:
            result[agent_id] = session_id
    return result


def _live_agent_session_optional_readiness_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
) -> dict[str, object] | None:
    meeting_id = str(payload.get("meeting_id") or "").strip()
    group_id = str(payload.get("group_id") or "").strip()
    if not meeting_id or not group_id:
        return None
    try:
        return live_agent_session_readiness_payload(
            output_root,
            process_supervisor,
            meeting_id=meeting_id,
            group_id=group_id,
        )
    except ValueError as error:
        if "was not found" in str(error):
            if payload.get("_meeting_id_resolved_from_group"):
                raise
            return None
        raise


def _live_agent_session_ensured_readiness_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    session: dict[str, object],
) -> dict[str, object]:
    meeting_id = str(session.get("meeting_id") or payload.get("meeting_id") or "").strip()
    group_id = str(session.get("group_id") or payload.get("group_id") or "").strip()
    if meeting_id and group_id:
        ensured = live_agent_session_readiness_payload(
            output_root,
            process_supervisor,
            meeting_id=meeting_id,
            group_id=group_id,
        )
    else:
        ensured = dict(session)
    for key in ("reply_probe", "auto_rounds", "finalization"):
        value = session.get(key)
        if isinstance(value, dict):
            ensured[key] = value
    return ensured


def live_agent_session_check_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
) -> dict[str, object]:
    group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        raise ValueError("Live agent group id is required.")
    return _session_check_payload_with_process_reason(
        output_root,
        process_supervisor,
        meeting_id=str(payload.get("meeting_id") or ""),
        group_id=group_id,
    )


def live_agent_session_readiness_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    *,
    meeting_id: str,
    group_id: str,
) -> dict[str, object]:
    if not str(group_id or "").strip():
        raise ValueError("Live agent group id is required.")
    return _session_check_payload_with_process_reason(
        output_root,
        process_supervisor,
        meeting_id=str(meeting_id or ""),
        group_id=str(group_id or ""),
    )


def _session_check_payload_with_process_reason(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    *,
    meeting_id: str,
    group_id: str,
) -> dict[str, object]:
    groups = _session_process_groups_snapshot(process_supervisor)
    session = check_live_agent_session(
        output_root,
        process_supervisor,
        meeting_id=meeting_id,
        group_id=group_id,
        groups=groups,
    )
    group_id = str(session.get("group_id") or "").strip()
    if not group_id or "process_reason" in session:
        return session
    group = _find_session_process_group(groups, group_id)
    reason = _live_agent_process_health_reason(group) if group else {}
    if not reason:
        return session
    return {**session, "process_reason": reason}


def _session_process_groups_snapshot(
    process_supervisor: LiveAgentProcessSupervisor,
) -> list[dict[str, object]]:
    if not hasattr(process_supervisor, "snapshot_groups"):
        return []
    groups = process_supervisor.snapshot_groups()
    return [group for group in groups if isinstance(group, dict)] if isinstance(groups, list) else []


def _find_session_process_group(
    groups: list[dict[str, object]],
    group_id: str,
) -> dict[str, object]:
    for group in groups:
        if str(group.get("group_id") or "") == group_id:
            return group
    return {}


def live_agent_session_restart_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
) -> dict[str, object]:
    group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        raise ValueError("Live agent group id is required.")
    session = restart_live_agent_session(
        output_root,
        process_supervisor,
        meeting_id=str(payload.get("meeting_id") or ""),
        group_id=group_id,
        connect_timeout_seconds=_payload_nonnegative_float(payload.get("connect_timeout_seconds"), 5.0),
    )
    return _attach_session_auto_rounds_if_requested(output_root, session, payload)


def live_agent_session_recover_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
) -> dict[str, object]:
    group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        raise ValueError("Live agent group id is required.")
    session = recover_live_agent_session(
        output_root,
        process_supervisor,
        meeting_id=str(payload.get("meeting_id") or ""),
        group_id=group_id,
        connect_timeout_seconds=_payload_nonnegative_float(payload.get("connect_timeout_seconds"), 5.0),
    )
    return _attach_session_auto_rounds_if_requested(output_root, session, payload)


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


def _attach_session_auto_rounds_if_requested(
    output_root: Path,
    session: dict[str, object],
    payload: dict[str, object],
) -> dict[str, object]:
    reply_probe = None
    if _payload_bool(payload.get("probe_bound_agents")):
        reply_probe = _session_bound_agent_reply_probe_payload(output_root, session, payload)
        session["reply_probe"] = reply_probe
    if not _payload_bool(payload.get("run_remaining_rounds")):
        if _payload_bool(payload.get("finalize_after_rounds")):
            session["finalization"] = _skipped_rounds_finalization_result(
                str(session.get("meeting_id") or ""),
                reason="rounds_not_requested",
            )
        return session
    auto_rounds_options = _session_auto_rounds_options(payload)
    if _operation_result_status(session.get("status")) != "ready":
        session["auto_rounds"] = _skipped_session_auto_rounds_result(
            session,
            auto_rounds_options,
            reason="session_not_ready",
        )
    elif reply_probe is not None and _operation_result_status(reply_probe.get("status")) != "ok":
        session["auto_rounds"] = _skipped_session_auto_rounds_result(
            session,
            auto_rounds_options,
            reason="probe_not_ready",
        )
    else:
        session["auto_rounds"] = live_agent_turn_rounds_payload(
            output_root,
            str(session.get("meeting_id") or ""),
            auto_rounds_options,
        )
    finalization = _rounds_finalization_result_if_requested(
        output_root,
        str(session.get("meeting_id") or ""),
        session["auto_rounds"],
        payload,
    )
    if finalization is not None:
        session["finalization"] = finalization
    return session


def _rounds_finalization_result_if_requested(
    output_root: Path,
    meeting_id: str,
    rounds_result: dict[str, object],
    payload: dict[str, object],
) -> dict[str, object] | None:
    if not _payload_bool(payload.get("finalize_after_rounds")):
        return None
    clean_meeting_id = clean_lobby_text(rounds_result.get("meeting_id") or meeting_id, limit=128)
    if _operation_result_status(rounds_result.get("status")) not in {"answered", "complete"}:
        return _skipped_rounds_finalization_result(clean_meeting_id, reason="rounds_not_ready")
    try:
        meeting_dir = _safe_meeting_dir(output_root, clean_meeting_id)
        meeting = _read_meeting_record(meeting_dir)
    except ValueError as error:
        reason = clean_lobby_text(str(error), limit=256) or "finalization_failed"
        return _failed_rounds_finalization_result(clean_meeting_id, reason=reason)
    except (OSError, json.JSONDecodeError):
        return _failed_rounds_finalization_result(clean_meeting_id, reason="finalization_failed")
    if remaining_official_round_ids(meeting, max_rounds=None):
        return _skipped_rounds_finalization_result(clean_meeting_id, reason="rounds_still_remaining")
    try:
        return finalize_live_agent_meeting(meeting_dir)
    except ValueError as error:
        reason = clean_lobby_text(str(error), limit=256) or "finalization_failed"
        return _failed_rounds_finalization_result(clean_meeting_id, reason=reason)
    except (OSError, json.JSONDecodeError):
        return _failed_rounds_finalization_result(clean_meeting_id, reason="finalization_failed")


def _skipped_rounds_finalization_result(meeting_id: str, *, reason: str) -> dict[str, object]:
    return {
        "status": "skipped",
        "reason": clean_lobby_text(reason, limit=128),
        "meeting_id": clean_lobby_text(meeting_id, limit=128),
        "official_event_count": 0,
    }


def _failed_rounds_finalization_result(meeting_id: str, *, reason: str) -> dict[str, object]:
    return {
        "status": "failed",
        "reason": clean_lobby_text(reason, limit=256) or "finalization_failed",
        "meeting_id": clean_lobby_text(meeting_id, limit=128),
        "official_event_count": 0,
    }


def _session_bound_agent_reply_probe_payload(
    output_root: Path,
    session: dict[str, object],
    payload: dict[str, object],
) -> dict[str, object]:
    timeout_seconds = safe_probe_timeout(
        _payload_nonnegative_float(payload.get("probe_timeout_seconds", payload.get("probe_timeout")), 12.0)
    )
    agent_ids = _session_bound_agent_ids(session)
    if _operation_result_status(session.get("status")) != "ready":
        return _session_reply_probe_summary(
            agent_ids,
            [],
            timeout_seconds=timeout_seconds,
            status="skipped",
            reason="session_not_ready",
        )
    if not agent_ids:
        return _session_reply_probe_summary(
            agent_ids,
            [],
            timeout_seconds=timeout_seconds,
            status="skipped",
            reason="no_bound_agents",
        )
    probes = []
    for agent_id in agent_ids:
        try:
            probe = _run_session_bound_agent_probe(output_root, agent_id, timeout_seconds=timeout_seconds)
        except ValueError:
            probe = {"status": "failed", "agent_id": agent_id, "reason": "probe could not be run"}
        probes.append(_safe_readiness_probe_result(probe))
    status = "ok" if probes and all(_operation_result_status(probe.get("status")) == "ok" for probe in probes) else "failed"
    return _session_reply_probe_summary(agent_ids, probes, timeout_seconds=timeout_seconds, status=status)


def _session_bound_agent_ids(session: dict[str, object]) -> list[str]:
    connection = session.get("connection") if isinstance(session.get("connection"), dict) else {}
    for key in ("agent_ids", "connected_agent_ids"):
        agent_ids = _safe_payload_strings(connection.get(key), limit=64)
        if agent_ids:
            return agent_ids
    process = session.get("process") if isinstance(session.get("process"), dict) else {}
    return _safe_payload_strings(process.get("agent_ids"), limit=64)


def _run_session_bound_agent_probe(output_root: Path, agent_id: str, *, timeout_seconds: float) -> dict[str, object]:
    previous_engagement = _live_agent_engagement_snapshot(output_root, agent_id)
    previous_mode = str(previous_engagement.get("engagement_mode") or "")
    switch_for_probe = previous_mode in {"manual", "watch", "moderator_called"}
    if switch_for_probe:
        update_live_agent_engagement(output_root, agent_id, "human_only")
    try:
        return run_live_agent_probe(output_root, agent_id, timeout_seconds=timeout_seconds)
    finally:
        if switch_for_probe:
            _restore_live_agent_engagement_snapshot(output_root, agent_id, previous_engagement)


def _live_agent_engagement_snapshot(output_root: Path, agent_id: str) -> dict[str, object]:
    clean_agent_id = str(agent_id or "").strip()
    state = _read_live_agent_presence_state(output_root)
    agents = state.get("agents")
    if isinstance(agents, list):
        for agent in agents:
            if isinstance(agent, dict) and str(agent.get("agent_id") or "") == clean_agent_id:
                snapshot: dict[str, object] = {"engagement_mode": str(agent.get("engagement_mode") or "")}
                if "engagement_mode_updated_at" in agent:
                    snapshot["engagement_mode_updated_at"] = str(agent.get("engagement_mode_updated_at") or "")
                return snapshot
    for agent in read_live_agents(output_root):
        if str(agent.get("agent_id") or "") == clean_agent_id:
            return {"engagement_mode": str(agent.get("engagement_mode") or "")}
    return {"engagement_mode": ""}


def _restore_live_agent_engagement_snapshot(
    output_root: Path,
    agent_id: str,
    snapshot: dict[str, object],
) -> None:
    clean_agent_id = str(agent_id or "").strip()
    if not clean_agent_id:
        return
    state = _read_live_agent_presence_state(output_root)
    agents = state.get("agents")
    if not isinstance(agents, list):
        return
    for agent in agents:
        if not isinstance(agent, dict) or str(agent.get("agent_id") or "") != clean_agent_id:
            continue
        agent["engagement_mode"] = str(snapshot.get("engagement_mode") or "")
        if "engagement_mode_updated_at" in snapshot:
            agent["engagement_mode_updated_at"] = str(snapshot.get("engagement_mode_updated_at") or "")
        else:
            agent.pop("engagement_mode_updated_at", None)
        _write_live_agent_presence_state(output_root, state)
        return


def _read_live_agent_presence_state(output_root: Path) -> dict[str, object]:
    path = output_root / "live_agents.json"
    if not path.exists():
        return {"agents": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"agents": []}
    return data if isinstance(data, dict) else {"agents": []}


def _write_live_agent_presence_state(output_root: Path, state: dict[str, object]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / "live_agents.json"
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)


def _session_reply_probe_summary(
    agent_ids: list[str],
    probes: list[dict[str, object]],
    *,
    timeout_seconds: float,
    status: str,
    reason: str = "",
) -> dict[str, object]:
    ok_count = sum(1 for probe in probes if _operation_result_status(probe.get("status")) == "ok")
    timeout_count = sum(1 for probe in probes if _operation_result_status(probe.get("status")) == "timeout")
    skipped_count = sum(1 for probe in probes if _operation_result_status(probe.get("status")) == "skipped")
    failed_count = sum(
        1
        for probe in probes
        if _operation_result_status(probe.get("status")) not in {"ok", "timeout", "skipped"}
    )
    summary: dict[str, object] = {
        "status": status,
        "agent_ids": agent_ids,
        "probe_count": len(probes),
        "ok_count": ok_count,
        "timeout_count": timeout_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "timeout_seconds": timeout_seconds,
        "probes": probes,
    }
    if reason:
        summary["reason"] = clean_lobby_text(reason, limit=128)
    return summary


def _skipped_session_auto_rounds_result(
    session: dict[str, object],
    options: dict[str, object],
    *,
    reason: str = "session_not_ready",
) -> dict[str, object]:
    return {
        "status": "skipped",
        "reason": clean_lobby_text(reason, limit=128),
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
        "lobby_events": read_lobby(output_root, limit=LIVE_AGENT_ROOM_LOBBY_EVENT_LIMIT),
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
    actor_id = str(agent.get("agent_id") or agent_id)
    source_event_id = clean_lobby_text(payload.get("source_event_id"), limit=128)
    with LIVE_AGENT_LOBBY_LOCK:
        existing_event = _existing_live_agent_lobby_reply(output_root, actor_id=actor_id, source_event_id=source_event_id)
        if existing_event is not None:
            updated_agent = heartbeat_live_agent(
                output_root,
                actor_id,
                status="online",
                metadata={
                    "last_error": "",
                    "last_reply_at": existing_event.get("created_at") or datetime.now(UTC).isoformat(),
                    "last_observed_event_id": source_event_id,
                },
            )
            return {"agent": updated_agent, "event": existing_event, "events": read_lobby(output_root)}
        event = append_lobby_event(
            output_root,
            {
                "name": agent.get("display_name") or agent.get("agent_id") or agent_id,
                "side": "other-agent",
                "kind": payload.get("kind") or "message",
                "message": message,
                "actor_id": actor_id,
                "source_event_id": source_event_id,
                "auto_chain_depth": payload.get("auto_chain_depth") or 0,
            },
            live_agent_endpoint=True,
        )
        reply_metadata: dict[str, object] = {
            "last_error": "",
            "last_reply_at": event.get("created_at") or datetime.now(UTC).isoformat(),
        }
        event_source_id = clean_lobby_text(event.get("source_event_id"), limit=128)
        if event_source_id:
            reply_metadata["last_observed_event_id"] = event_source_id
        updated_agent = heartbeat_live_agent(
            output_root,
            actor_id,
            status="online",
            metadata=reply_metadata,
        )
        return {"agent": updated_agent, "event": event, "events": read_lobby(output_root)}


def _existing_live_agent_lobby_reply(output_root: Path, *, actor_id: str, source_event_id: str) -> dict[str, object] | None:
    if not source_event_id:
        return None
    for event in reversed(read_lobby(output_root, limit=None)):
        if not isinstance(event, dict):
            continue
        if str(event.get("actor_id") or "") != actor_id:
            continue
        if clean_lobby_text(event.get("source_event_id"), limit=128) != source_event_id:
            continue
        if event.get("live_agent_endpoint") is not True:
            continue
        return event
    return None


def live_agent_turn_request_payload(output_root: Path, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    meeting_dir = _safe_meeting_dir(output_root, clean_meeting_id)
    if not clean_meeting_id or not meeting_dir.exists():
        raise ValueError(f"Meeting {clean_meeting_id or '(blank)'} was not found.")
    with _live_agent_round_scheduler_lock(clean_meeting_id):
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
        event_payload: dict[str, object] = {
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
        }
        review_checkpoint_id = clean_lobby_text(payload.get("review_checkpoint_id") or payload.get("checkpoint_id"), limit=128)
        if review_checkpoint_id:
            event_payload.update(
                {
                    "review_checkpoint_id": review_checkpoint_id,
                    "channel": "review",
                    "official_record": False,
                }
            )
        event = append_live_event(meeting_dir, event_payload)
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


def live_agent_review_checkpoint_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    meeting_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    meeting_dir = _safe_meeting_dir(output_root, clean_meeting_id)
    if not clean_meeting_id or not meeting_dir.exists():
        raise ValueError(f"Meeting {clean_meeting_id or '(blank)'} was not found.")
    group_id = clean_live_agent_group_id(str(payload.get("group_id") or ""))
    if not group_id:
        raise ValueError("Live agent group id is required.")
    content = clean_lobby_text(payload.get("content") or payload.get("message"), limit=4000)
    if not content:
        raise ValueError("Review checkpoint content is required.")
    timeout_seconds = _payload_nonnegative_float(payload.get("timeout_seconds", payload.get("timeout")), 30.0)
    checkpoint_id = clean_lobby_text(payload.get("checkpoint_id") or payload.get("review_checkpoint_id"), limit=128)
    if not checkpoint_id:
        checkpoint_id = f"review-{uuid4().hex[:8]}"
    readiness = live_agent_session_readiness_payload(
        output_root,
        process_supervisor,
        meeting_id=clean_meeting_id,
        group_id=group_id,
    )
    expected_agent_ids = _review_checkpoint_expected_agent_ids(readiness)
    if readiness.get("status") != "ready":
        return {
            "status": "degraded",
            "reason": "session_not_ready",
            "checkpoint_id": checkpoint_id,
            "meeting_id": clean_meeting_id,
            "group_id": group_id,
            "turn_count": 0,
            "answered_count": 0,
            "timeout_count": 0,
            "skipped_count": 0,
            "timeout_seconds": timeout_seconds,
            "agent_ids": [],
            "expected_agent_ids": expected_agent_ids,
            "results": [],
            "readiness": readiness,
        }

    target_agent_ids = _review_checkpoint_target_agent_ids(payload.get("agent_ids"), expected_agent_ids)
    identities = _review_checkpoint_agent_identities(_read_meeting_record(meeting_dir))
    results = []
    for index, agent_id in enumerate(target_agent_ids):
        identity = identities.get(agent_id, {})
        request = live_agent_turn_request_payload(
            output_root,
            clean_meeting_id,
            {
                "agent_id": agent_id,
                "role_id": clean_lobby_text(identity.get("role_id"), limit=128) or agent_id,
                "display_name": clean_lobby_text(identity.get("display_name"), limit=64) or agent_id,
                "turn_id": checkpoint_id,
                "turn_index": index,
                "content": content,
                "review_checkpoint_id": checkpoint_id,
            },
        )
        request_event = request.get("event") if isinstance(request.get("event"), dict) else {}
        source_event_id = clean_lobby_text(request_event.get("id"), limit=128)
        if not source_event_id:
            raise ValueError("Review checkpoint request could not be created.")
        wait_result = wait_for_review_checkpoint_reply(
            meeting_dir,
            agent_id=agent_id,
            source_event_id=source_event_id,
            checkpoint_id=checkpoint_id,
            timeout_seconds=timeout_seconds,
        )
        results.append(
            _live_agent_turn_sequence_result(
                index,
                {
                    "status": wait_result["status"],
                    "request_event": request_event,
                    "reply_event": wait_result["reply_event"],
                    "elapsed_seconds": wait_result["elapsed_seconds"],
                    "timeout_seconds": wait_result["timeout_seconds"],
                },
            )
        )
    answered_count = sum(1 for result in results if result["status"] == "answered")
    timeout_count = sum(1 for result in results if result["status"] == "timeout")
    skipped_count = sum(1 for result in results if result["status"] == "skipped")
    return {
        "status": _live_agent_turn_sequence_status(answered_count, timeout_count, skipped_count),
        "checkpoint_id": checkpoint_id,
        "meeting_id": clean_meeting_id,
        "group_id": group_id,
        "turn_count": len(target_agent_ids),
        "answered_count": answered_count,
        "timeout_count": timeout_count,
        "skipped_count": skipped_count,
        "timeout_seconds": timeout_seconds,
        "agent_ids": target_agent_ids,
        "results": results,
        "readiness": readiness,
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
        existing_reply = _live_agent_reply_for_request(meeting_dir, agent_id, source_event_id, request_event)
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
            event_payload: dict[str, object] = {
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
            }
            review_checkpoint_id = clean_lobby_text(request_event.get("review_checkpoint_id"), limit=128)
            if review_checkpoint_id:
                event_payload.update(
                    {
                        "review_checkpoint_id": review_checkpoint_id,
                        "channel": "review",
                        "official_record": False,
                    }
                )
            event = append_live_event(meeting_dir, event_payload)
    updated_agent = heartbeat_live_agent(
        output_root,
        agent_id,
        status="online",
        metadata={
            "last_error": "",
            "last_reply_at": datetime.now(UTC).isoformat(),
            "last_observed_live_event_id": source_event_id,
        },
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


def live_agent_discovery_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    report = build_discovered_live_agent_config(
        server=str(payload.get("server") or default_server),
        meeting_id=str(payload.get("meeting_id") or ""),
        engagement_mode=str(payload.get("engagement_mode") or "mentioned"),
        include_legacy_gemini=_payload_bool(payload.get("include_legacy_gemini")),
    )
    output_path = output_root / "live-agents.discovered.local.json"
    should_write = not ("write_config" in payload and not _payload_bool(payload.get("write_config")))
    if report.get("status") == "ok" and should_write:
        write_agent_config(output_path, report["config"])
        fill_discovery_next_command_output(report, str(output_path))
        report["output"] = str(output_path)
        report["written"] = True
        if _payload_bool(payload.get("session_bundle")):
            council_output, agent_output = discovered_session_bundle_paths(output_path)
            validate_distinct_session_bundle_paths(output_path, council_output, agent_output)
            bundle = build_discovered_session_bundle(report["config"])
            write_agent_config(council_output, bundle["council_config"])
            write_agent_config(agent_output, bundle["agent_config"])
            add_session_bundle_outputs(
                report,
                live_agent_output=str(output_path),
                council_output=str(council_output),
                agent_output=str(agent_output),
                server=str(payload.get("server") or default_server),
                meeting_id=str(payload.get("meeting_id") or ""),
                group_id=clean_live_agent_group_id(output_path.stem),
            )
    else:
        report["output"] = ""
        report["written"] = False
    return report


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
        lobby_probe_count=_payload_nonnegative_int(payload.get("lobby_probe_count"), 1),
        soak_cycle_count=_payload_session_smoke_soak_cycle_count(payload.get("soak_cycle_count")),
        soak_interval_seconds=_payload_session_smoke_soak_interval_seconds(payload.get("soak_interval_seconds")),
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
    session_smoke_requested = _payload_bool(payload.get("session_smoke"))
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
    session_smoke: dict[str, object] = {}
    if session_smoke_requested and smoke.get("status") == "ok":
        try:
            session_smoke = _safe_readiness_session_smoke_result(
                live_agent_session_smoke_payload(
                    output_root,
                    {
                        "timeout": _payload_nonnegative_float(payload.get("timeout"), 12.0),
                        "lobby_probe_count": _payload_nonnegative_int(payload.get("session_smoke_lobby_probe_count"), 1),
                        "soak_cycle_count": _payload_session_smoke_soak_cycle_count(
                            payload.get("session_smoke_soak_cycle_count")
                        ),
                        "soak_interval_seconds": _payload_session_smoke_soak_interval_seconds(
                            payload.get("session_smoke_soak_interval_seconds")
                        ),
                    },
                    default_server=default_server,
                )
            )
        except (LiveAgentSmokeFailed, ValueError, urllib.error.URLError):
            session_smoke = _safe_readiness_session_smoke_result(
                {
                    "status": "failed",
                    "error": SESSION_SMOKE_ERROR,
                }
            )
        checks.append({"id": "session_smoke", "status": session_smoke.get("status") or "unknown"})
    elif session_smoke_requested:
        session_smoke = {
            "status": "skipped",
            "reason": "smoke did not pass",
        }
        checks.append({"id": "session_smoke", "status": "skipped"})
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
    elif session_smoke_requested and session_smoke.get("status") != "ok":
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
    if session_smoke:
        result["session_smoke"] = session_smoke
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
    session_summary = _live_agent_session_health_summary(
        output_root,
        groups,
        diagnostic_group_ids=diagnostic_group_ids,
    )
    session_run_summary = _live_agent_session_run_health_summary(output_root)
    status = (
        "degraded"
        if agent_summary["attention"]
        or process_summary["attention"]
        or connection_summary["attention"]
        or session_summary["attention"]
        or session_run_summary["attention"]
        else "ok"
    )
    return {
        "status": status,
        "agents": agent_summary,
        "processes": process_summary,
        "connections": connection_summary,
        "sessions": session_summary,
        "session_runs": session_run_summary,
    }


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
    reasons = {}
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
            reason = _live_agent_process_health_reason(group)
            if reason:
                reasons[group_id] = reason
    return {"total": len(groups), "counts": counts, "attention": attention, "meeting_ids": meeting_ids, "reasons": reasons}


def _live_agent_process_health_reason(group: dict[str, object]) -> dict[str, str]:
    events = group.get("recent_events") if isinstance(group.get("recent_events"), list) else []
    group_id = str(group.get("group_id") or "").strip()
    status = str(group.get("status") or "").strip()
    seen_newer_event = False
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "").strip()
        if event_type in HEALTH_WATCHDOG_REASON_EVENT_TYPES:
            reason = _safe_health_watchdog_reason(event.get("reason"))
        elif event_type == HEALTH_RESTART_FAILED_REASON_EVENT_TYPE:
            if seen_newer_event or status != "error":
                continue
            reason = _safe_health_restart_failed_reason(group.get("last_error"), group_id=group_id)
        elif event_type == HEALTH_RECOVERED_UNKNOWN_REASON_EVENT_TYPE:
            if seen_newer_event or status != "unknown":
                continue
            reason = HEALTH_RECOVERED_UNKNOWN_REASON
        else:
            seen_newer_event = True
            continue
        if reason:
            return {"event_type": event_type, "reason": reason}
    return {}


def _safe_health_watchdog_reason(value: object) -> str:
    reason = clean_lobby_text(value, limit=160)
    if not reason or _looks_sensitive_health_watchdog_reason(reason):
        return ""
    return reason if SAFE_HEALTH_WATCHDOG_REASON_PATTERN.fullmatch(reason) else ""


def _looks_sensitive_health_watchdog_reason(reason: str) -> bool:
    lowered = reason.casefold()
    return "/" in reason or "\\" in reason or ".json" in lowered or "env:" in lowered


def _safe_health_restart_failed_reason(value: object, *, group_id: str) -> str:
    if not SAFE_HEALTH_RESTART_FAILED_GROUP_ID_PATTERN.fullmatch(group_id):
        return ""
    error = clean_lobby_text(value, limit=240)
    if not error or _looks_sensitive_health_restart_failed_error(error):
        return ""
    match = SAFE_HEALTH_RESTART_FAILED_ERROR_PATTERN.search(error)
    if not match or match.group(1) != group_id:
        return ""
    missing_kind = match.group(2)
    if missing_kind == "config":
        return "missing launch config"
    if missing_kind == "server":
        return "missing launch server"
    return ""


def _looks_sensitive_health_restart_failed_error(error: str) -> bool:
    lowered = error.casefold()
    secret_word = re.search(r"\b(auth|credential|password|secret|token)\b", lowered)
    return (
        bool(secret_word)
        or bool(re.search(r"(^|[\s:=])/", error))
        or "\\" in error
        or "://" in error
        or "--" in error
        or ".json" in lowered
        or "env:" in lowered
    )


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


def _live_agent_session_health_summary(
    output_root: Path,
    groups: list[dict[str, object]],
    *,
    diagnostic_group_ids: set[str] | None = None,
) -> dict[str, object]:
    diagnostic_group_ids = diagnostic_group_ids or set()
    visible_groups = [group for group in groups if not _is_diagnostic_process_group(group, diagnostic_group_ids)]
    summary = live_agent_session_readiness_summary(output_root, visible_groups)
    reasons_by_group = {
        str(group.get("group_id") or ""): _live_agent_process_health_reason(group)
        for group in visible_groups
    }
    for item in _as_dict_list(summary.get("items")):
        reason = reasons_by_group.get(str(item.get("group_id") or ""))
        if reason:
            item["process_reason"] = reason
    return summary


def _live_agent_session_run_health_summary(output_root: Path) -> dict[str, object]:
    snapshot = LiveAgentSessionRunController(output_root).health_snapshot()
    runs = snapshot.get("runs") if isinstance(snapshot.get("runs"), list) else []
    items = []
    attention = []
    retrying_count = 0
    for run in runs:
        if not isinstance(run, dict):
            continue
        status = str(run.get("status") or "unknown").strip() or "unknown"
        active = run.get("active") is True
        retrying = _live_agent_session_run_retrying(run)
        if retrying:
            retrying_count += 1
        if active and status != "ready":
            attention.append(_live_agent_session_run_attention_label(run, status=status, retrying=retrying))
        items.append(
            {
                "run_id": _safe_session_run_health_identity(run.get("run_id")),
                "meeting_id": _safe_session_run_health_identity(run.get("meeting_id")),
                "group_id": _safe_session_run_health_identity(run.get("group_id")),
                "status": clean_lobby_text(status, limit=64),
                "active": active,
                "phase": _safe_session_run_health_phase(run.get("phase")),
                "reconcile_failure_count": _safe_session_run_health_int(run.get("reconcile_failure_count")),
                "reconcile_backoff_seconds": _safe_session_run_health_int(run.get("reconcile_backoff_seconds")),
                "next_reconcile_at": _safe_session_run_health_timestamp(run.get("next_reconcile_at")),
            }
        )
    return {
        "total": _safe_session_run_health_int(snapshot.get("total")),
        "active": _safe_session_run_health_int(snapshot.get("active")),
        "ready": _safe_session_run_health_int(snapshot.get("ready")),
        "retrying": retrying_count,
        "attention": attention,
        "items": items,
    }


def _live_agent_session_run_retrying(run: dict[str, object]) -> bool:
    return (
        _safe_session_run_health_int(run.get("reconcile_failure_count")) > 0
        or _safe_session_run_health_int(run.get("reconcile_backoff_seconds")) > 0
        or bool(_safe_session_run_health_timestamp(run.get("next_reconcile_at")))
    )


def _live_agent_session_run_attention_label(run: dict[str, object], *, status: str, retrying: bool) -> str:
    parts = [
        _safe_session_run_health_identity(run.get("meeting_id")) or "-",
        _safe_session_run_health_identity(run.get("group_id")) or "-",
        _safe_session_run_health_identity(run.get("run_id")) or "-",
        clean_lobby_text(status, limit=64) or "unknown",
    ]
    if retrying:
        parts.append("retrying")
    return ":".join(parts)


def _safe_session_run_health_identity(value: object) -> str:
    text = clean_lobby_text(value, limit=128)
    if not text or text in {".", ".."}:
        return ""
    if text.casefold().startswith(("env:", "literal:")):
        return ""
    if _looks_sensitive_session_run_health_text(text):
        return ""
    if "/" in text or "\\" in text or Path(text).name != text:
        return ""
    return text


def _safe_session_run_health_phase(value: object) -> str:
    text = clean_lobby_text(value, limit=128)
    if not text or _looks_sensitive_session_run_health_text(text):
        return ""
    return text if re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", text) else ""


def _looks_sensitive_session_run_health_text(text: str) -> bool:
    lowered = text.casefold()
    token_like = re.search(
        r"\b(?:sk-[A-Za-z0-9_-]{6,}|gh[opusr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
        text,
    )
    return (
        bool(token_like)
        or _looks_sensitive_process_control_error(text)
        or "literal:" in lowered
    )


def _safe_session_run_health_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _safe_session_run_health_timestamp(value: object) -> str:
    timestamp = clean_lobby_text(value, limit=64)
    return timestamp if re.fullmatch(r"[0-9T:+.\-Z]{1,64}", timestamp) else ""


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
    group_meeting_id = _safe_process_meeting_id(group.get("meeting_id"))
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
        if group_meeting_id and str(agent.get("meeting_id") or "") != group_meeting_id:
            attention.append({"agent_id": agent_id, "status": "wrong_meeting"})
            continue
        if _agent_last_seen_before_group_start(agent, group):
            attention.append({"agent_id": agent_id, "status": "not_reconnected"})
            continue
        status = str(agent.get("status") or "offline")
        if status in {"online", "working"}:
            connected += 1
            continue
        if status not in {"error", "stale", "offline"}:
            status = "offline"
        attention.append({"agent_id": agent_id, "status": status})
    return {"expected": expected, "connected": connected, "attention": attention}


def _agent_last_seen_before_group_start(agent: dict[str, object], group: dict[str, object]) -> bool:
    group_started_at = _parse_public_timestamp(group.get("started_at"))
    agent_last_seen_at = _parse_public_timestamp(agent.get("last_seen_at"))
    if group_started_at is None or agent_last_seen_at is None:
        return False
    return agent_last_seen_at < group_started_at


def _parse_public_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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


def stop_running_live_agent_processes_payload(
    process_supervisor: LiveAgentProcessSupervisor,
    *,
    output_root: Path | None = None,
) -> dict[str, object]:
    result = process_supervisor.stop_running_groups()
    response = {"result": result, "groups": process_supervisor.list_groups()}
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


def codex_session_join_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    meeting_id = _clean_codex_join_meeting_id(payload.get("meeting_id"))
    role_id = str(payload.get("role_id") or "")
    session_id = str(payload.get("session_id") or "")
    with _live_agent_round_scheduler_lock(meeting_id):
        meeting_dir = _codex_join_meeting_dir(output_root, meeting_id)
        meeting = _read_meeting_record(meeting_dir)
        _validate_codex_join_pre_round(meeting_dir, meeting)

        config_path = output_root / "codex-live-session.local.json"
        live_agent_config_path = output_root / DEFAULT_LIVE_AGENT_CONFIG_PATH.name
        effective_server = str(payload.get("server") or default_server)
        role_ids = _codex_invite_role_ids(output_root, meeting_id)
        config = build_codex_live_invite_config(
            session_id=session_id,
            role_id=role_id,
            role_ids=role_ids,
            existing=_codex_join_agent_config_from_meeting(meeting),
        )
        resident_config = build_codex_live_agent_config(
            config,
            server=effective_server,
            meeting_id=meeting_id,
            engagement_mode=str(payload.get("engagement_mode") or "always"),
        )
        write_agent_config(config_path, config)
        write_agent_config(live_agent_config_path, resident_config)
        write_live_state(meeting_dir, _meeting_with_codex_live_config(meeting, config, config_path=config_path))

        group_id = clean_live_agent_group_id(live_agent_config_path.stem)
        session_payload = {
            "server": effective_server,
            "meeting_id": meeting_id,
            "group_id": group_id,
            "live_agent_config_path": str(live_agent_config_path),
            "connect_timeout_seconds": _payload_nonnegative_float(payload.get("connect_timeout_seconds"), 5.0),
            "auto_restart": _payload_bool(payload.get("auto_restart")),
            "max_restarts": _payload_nonnegative_int(payload.get("max_restarts"), 0),
            "restart_backoff_seconds": _payload_nonnegative_float(payload.get("restart_backoff_seconds"), 5.0),
            "stale_restart_after_seconds": _payload_nonnegative_float(payload.get("stale_restart_after_seconds"), 0.0),
        }
        binding = _binding_for_role(config.get("agent_bindings", []), role_id)
        if _codex_join_needs_session_restart(output_root, process_supervisor, group_id=group_id, binding=binding):
            session = live_agent_session_restart_payload(output_root, process_supervisor, session_payload)
            session["action"] = "restart"
        else:
            session = live_agent_session_ensure_payload(
                output_root,
                process_supervisor,
                session_payload,
                default_server=effective_server,
            )
        session["config_path"] = str(config_path)
        session["live_agent_config_path"] = str(live_agent_config_path)
        session["invite"] = {
            "config_path": str(config_path),
            "live_agent_config_path": str(live_agent_config_path),
            "group_id": group_id,
            "binding": binding,
        }
        return session


def _clean_codex_join_meeting_id(value: object) -> str:
    meeting_id = clean_lobby_text(value, limit=128)
    if not meeting_id or meeting_id in {".", ".."}:
        raise ValueError("Meeting was not found.")
    if "/" in meeting_id or "\\" in meeting_id or Path(meeting_id).name != meeting_id:
        raise ValueError(f"Meeting {meeting_id} was not found.")
    return meeting_id


def _codex_join_meeting_dir(output_root: Path, meeting_id: str) -> Path:
    meetings_root = (output_root / "meetings").resolve()
    meeting_dir = (meetings_root / meeting_id).resolve()
    try:
        meeting_dir.relative_to(meetings_root)
    except ValueError as error:
        raise ValueError(f"Meeting {meeting_id} was not found.") from error
    if not meeting_dir.exists() or not meeting_dir.is_dir():
        raise ValueError(f"Meeting {meeting_id} was not found.")
    if not (meeting_dir / "live_state.json").exists():
        raise ValueError("Codex live session join requires a live pre-round meeting.")
    return meeting_dir


def _validate_codex_join_pre_round(meeting_dir: Path, meeting: dict[str, object]) -> None:
    if clean_lobby_text(meeting.get("live_status"), limit=64) not in {"running", "stalled"}:
        raise ValueError("Codex live session join requires a live pre-round meeting.")
    if _as_dict_list(meeting.get("debate_rounds")):
        raise ValueError("Codex live session join is only available before official rounds begin.")
    for event in read_live_events(meeting_dir, limit=None):
        if event.get("official_record") is True or event.get("channel") == "official" or event.get("kind") == "live_agent_turn_request":
            raise ValueError("Codex live session join is only available before official rounds begin.")


def _codex_join_agent_config_from_meeting(meeting: dict[str, object]) -> dict[str, object]:
    return {
        "providers": _config_map_values(meeting.get("provider_configs")),
        "permission_profiles": _config_map_values(meeting.get("permission_profiles")),
        "agent_bindings": [
            binding
            for binding in _as_dict_list(meeting.get("agent_bindings"))
            if binding.get("provider_id") == CODEX_LIVE_PROVIDER_ID
        ],
    }


def _codex_join_needs_session_restart(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    *,
    group_id: str,
    binding: dict[str, object],
) -> bool:
    group = _find_session_process_group(_session_process_groups_snapshot(process_supervisor), group_id)
    if str(group.get("status") or "") not in {"running", "restarting"}:
        return False
    agent_id = str(binding.get("agent_id") or "").strip()
    requested_session_id = str(binding.get("session_id") or "").strip()
    if not agent_id or not requested_session_id:
        return False
    for agent in read_live_agents(output_root):
        if str(agent.get("agent_id") or "") != agent_id:
            continue
        return str(agent.get("session_id") or "").strip() != requested_session_id
    return False


def _meeting_with_codex_live_config(
    meeting: dict[str, object],
    config: dict[str, object],
    *,
    config_path: Path,
) -> dict[str, object]:
    updated = dict(meeting)
    updated["provider_configs"] = _dicts_by_id(config.get("providers"))
    updated["permission_profiles"] = _dicts_by_id(config.get("permission_profiles"))
    updated["agent_bindings"] = _as_dict_list(config.get("agent_bindings"))
    updated["agent_config_source"] = str(config_path)
    return updated


def _config_map_values(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        return [dict(item) for item in value.values() if isinstance(item, dict)]
    return _as_dict_list(value)


def _dicts_by_id(value: object) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for item in _as_dict_list(value):
        item_id = str(item.get("id") or "").strip()
        if item_id:
            result[item_id] = item
    return result


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


def _codex_session_invite_operation_details(invite: dict[str, object]) -> dict[str, object]:
    binding = invite.get("binding") if isinstance(invite.get("binding"), dict) else {}
    return {
        "role_id": clean_lobby_text(binding.get("role_id"), limit=128),
        "agent_id": clean_lobby_text(binding.get("agent_id"), limit=128),
        "join_mode": clean_lobby_text(binding.get("join_mode"), limit=64),
        "provider_id": clean_lobby_text(binding.get("provider_id"), limit=128),
    }


def _codex_session_invite_error_details(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    role_id = clean_lobby_text(payload.get("role_id"), limit=128)
    meeting_id = _optional_str(payload.get("meeting_id"))
    try:
        known_role_ids = set(_codex_invite_role_ids(output_root, meeting_id))
    except ValueError:
        known_role_ids = set()
    return {"role_id": role_id} if role_id in known_role_ids else {}


def _codex_session_join_operation_details(join: dict[str, object]) -> dict[str, object]:
    invite = join.get("invite") if isinstance(join.get("invite"), dict) else {}
    details = _codex_session_invite_operation_details(invite)
    details.update(
        {
            "meeting_id": clean_lobby_text(join.get("meeting_id"), limit=128),
            "group_id": clean_lobby_text(join.get("group_id"), limit=128),
            "result_status": _operation_result_status(join.get("status")),
        }
    )
    ensure_action = clean_lobby_text(join.get("action"), limit=64)
    if ensure_action:
        details["ensure_action"] = ensure_action
    return details


def _codex_session_join_error_details(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    details: dict[str, object] = {}
    meeting_id = clean_lobby_text(payload.get("meeting_id"), limit=128)
    role_id = clean_lobby_text(payload.get("role_id"), limit=128)
    try:
        _codex_join_meeting_dir(output_root, meeting_id)
        details["meeting_id"] = meeting_id
        if role_id in set(_codex_invite_role_ids(output_root, meeting_id)):
            details["role_id"] = role_id
    except ValueError:
        pass
    return details


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
        if not is_official_turn_reply_event(event):
            continue
        if str(event.get("actor_id") or "") != agent_id:
            continue
        if str(event.get("source_event_id") or "") != source_event_id:
            continue
        return event
    return None


def _live_agent_reply_for_request(
    meeting_dir: Path,
    agent_id: str,
    source_event_id: str,
    request_event: dict[str, object],
) -> dict[str, object] | None:
    checkpoint_id = clean_lobby_text(request_event.get("review_checkpoint_id"), limit=128)
    if checkpoint_id:
        return _review_checkpoint_reply_for_request(meeting_dir, agent_id, source_event_id, checkpoint_id)
    return _official_turn_reply_for_request(meeting_dir, agent_id, source_event_id)


def _review_checkpoint_reply_for_request(
    meeting_dir: Path,
    agent_id: str,
    source_event_id: str,
    checkpoint_id: str,
) -> dict[str, object] | None:
    for event in read_live_events(meeting_dir, limit=None):
        if not is_review_checkpoint_reply_event(event):
            continue
        if str(event.get("actor_id") or "") != agent_id:
            continue
        if str(event.get("source_event_id") or "") != source_event_id:
            continue
        if clean_lobby_text(event.get("review_checkpoint_id"), limit=128) != checkpoint_id:
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


def _meeting_finalize_path(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[0] == "api" and parts[1] == "meetings" and parts[3] == "finalize":
        return unquote(parts[2])
    return None


def _meeting_review_checkpoint_path(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[0] == "api" and parts[1] == "meetings" and parts[3] == "review-checkpoints":
        return unquote(parts[2])
    return None


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


def _live_agent_session_run_action_path(path: str, action: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[0] == "api" and parts[1] == "live-agent-session-runs" and parts[3] == action:
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


def _operation_group_ids(records: object) -> list[str]:
    if not isinstance(records, list):
        return []
    group_ids = []
    for record in records:
        if not isinstance(record, dict):
            continue
        group_id = str(record.get("group_id") or "").strip()
        if group_id:
            group_ids.append(group_id)
    return group_ids


def _process_offline_operation_details(summary: object) -> dict[str, object]:
    if not isinstance(summary, dict):
        return {}
    expected = _payload_nonnegative_int(summary.get("expected"), 0)
    offline = _payload_nonnegative_int(summary.get("offline"), 0)
    skipped = _payload_nonnegative_int(summary.get("skipped"), 0)
    offline_agent_ids = _safe_payload_strings(summary.get("offline_agent_ids"), limit=64)
    attention = _process_offline_attention(summary.get("attention"))
    if expected <= 0 and offline <= 0 and skipped <= 0 and not offline_agent_ids and not attention:
        return {}
    return {
        "offline_expected_agent_count": expected,
        "offline_agent_count": offline,
        "offline_skipped_agent_count": skipped,
        "offline_agent_ids": offline_agent_ids,
        "offline_attention": attention,
    }


def _process_bulk_offline_operation_details(records: object) -> dict[str, object]:
    if not isinstance(records, list):
        return {}
    expected = 0
    offline = 0
    skipped = 0
    offline_agent_ids: list[str] = []
    attention: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        summary = record.get("offline")
        if not isinstance(summary, dict):
            continue
        expected += _payload_nonnegative_int(summary.get("expected"), 0)
        offline += _payload_nonnegative_int(summary.get("offline"), 0)
        skipped += _payload_nonnegative_int(summary.get("skipped"), 0)
        offline_agent_ids.extend(_safe_payload_strings(summary.get("offline_agent_ids"), limit=64))
        attention.extend(_process_offline_attention(summary.get("attention")))
    if expected <= 0 and offline <= 0 and skipped <= 0 and not offline_agent_ids and not attention:
        return {}
    return {
        "offline_expected_agent_count": expected,
        "offline_agent_count": offline,
        "offline_skipped_agent_count": skipped,
        "offline_agent_ids": offline_agent_ids,
        "offline_attention": attention,
    }


def _process_offline_attention(value: object) -> list[str]:
    attention: list[str] = []
    for item in _as_dict_list(value):
        agent_id = clean_lobby_text(item.get("agent_id"), limit=64)
        status = clean_lobby_text(item.get("status"), limit=64)
        if agent_id and status:
            attention.append(f"{agent_id}:{status}")
    return attention


def _process_stop_running_operation_status(result: dict[str, object]) -> str:
    failed_count = _payload_nonnegative_int(result.get("failed_count"), 0)
    stopped_count = _payload_nonnegative_int(result.get("stopped_count"), 0)
    return "success" if failed_count == 0 else "degraded" if stopped_count else "failed"


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
    }
    error = str(smoke.get("error") or "").strip()[:240]
    if error:
        safe["error"] = OFFICIAL_ROUND_SMOKE_ERROR
    reason = str(smoke.get("reason") or "").strip()[:128]
    if reason:
        safe["reason"] = reason
    return safe


def _safe_readiness_session_smoke_result(smoke: dict[str, object]) -> dict[str, object]:
    safe = {
        "status": str(smoke.get("status") or "unknown"),
        "meeting_id": clean_lobby_text(smoke.get("meeting_id"), limit=128),
        "group_id": clean_lobby_text(smoke.get("group_id"), limit=128),
        "agent_ids": _safe_payload_strings(smoke.get("agent_ids"), limit=64),
        "terminal_session_supported": smoke.get("terminal_session_supported") is True,
        "terminal_session_included": smoke.get("terminal_session_included") is True,
        "terminal_session_status": _operation_result_status(smoke.get("terminal_session_status")),
        "terminal_session_reason": clean_lobby_text(smoke.get("terminal_session_reason"), limit=128),
        "rounds_status": _operation_result_status(smoke.get("rounds_status")),
        "answered_round_count": _payload_nonnegative_int(smoke.get("answered_round_count"), 0),
        "lobby_probe_count": _payload_nonnegative_int(smoke.get("lobby_probe_count"), 1),
        "expected_reply_count": _payload_nonnegative_int(smoke.get("expected_reply_count"), 0),
        "self_service_official_reply_count": _payload_nonnegative_int(smoke.get("self_service_official_reply_count"), 0),
        "self_service_lobby_reply_count": _payload_nonnegative_int(smoke.get("self_service_lobby_reply_count"), 0),
        "self_service_post_restart_reply_count": _payload_nonnegative_int(
            smoke.get("self_service_post_restart_reply_count"),
            0,
        ),
        "self_service_post_recover_reply_count": _payload_nonnegative_int(
            smoke.get("self_service_post_recover_reply_count"),
            0,
        ),
        "self_service_soak_reply_count": _payload_nonnegative_int(smoke.get("self_service_soak_reply_count"), 0),
        "reply_count": _payload_nonnegative_int(smoke.get("reply_count"), 0),
        "post_restart_reply_count": _payload_nonnegative_int(smoke.get("post_restart_reply_count"), 0),
        "post_recover_reply_count": _payload_nonnegative_int(smoke.get("post_recover_reply_count"), 0),
        "soak_cycle_count": _payload_nonnegative_int(smoke.get("soak_cycle_count"), 0),
        "soak_reply_count": _payload_nonnegative_int(smoke.get("soak_reply_count"), 0),
        "soak_check_statuses": _safe_payload_strings(smoke.get("soak_check_statuses"), limit=32),
        "start_status": _operation_result_status(smoke.get("start_status")),
        "check_status": _operation_result_status(smoke.get("check_status")),
        "resume_status": _operation_result_status(smoke.get("resume_status")),
        "restart_status": _operation_result_status(smoke.get("restart_status")),
        "recover_status": _operation_result_status(smoke.get("recover_status")),
        "stop_status": _operation_result_status(smoke.get("stop_status")),
        "post_stop_process_status": _operation_result_status(smoke.get("post_stop_process_status")),
    }
    error = str(smoke.get("error") or "").strip()
    if error:
        safe["error"] = SESSION_SMOKE_ERROR
    reason = clean_lobby_text(smoke.get("reason"), limit=128)
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


def _readiness_health_operation_details(health: object) -> dict[str, object]:
    if not isinstance(health, dict):
        return {}
    details: dict[str, object] = {"health_status": _operation_result_status(health.get("status"))}
    detail_names = {
        "agents": "agent",
        "processes": "process",
        "connections": "connection",
        "sessions": "session",
    }
    for section_name, detail_name in detail_names.items():
        section = health.get(section_name)
        if not isinstance(section, dict):
            continue
        attention = _safe_health_operation_strings(section.get("attention"), limit=128)
        if attention:
            details[f"health_{detail_name}_attention"] = attention
    process_reasons = _health_process_reason_labels(health.get("processes"))
    if process_reasons:
        details["health_process_reasons"] = process_reasons
    return details


def _health_process_reason_labels(processes: object) -> list[str]:
    if not isinstance(processes, dict):
        return []
    reasons = processes.get("reasons")
    if not isinstance(reasons, dict):
        return []
    labels = []
    for group_id, reason_payload in reasons.items():
        clean_group_id = clean_lobby_text(group_id, limit=64)
        if not clean_group_id:
            continue
        if isinstance(reason_payload, dict):
            event_type = clean_lobby_text(reason_payload.get("event_type"), limit=64)
            reason = clean_lobby_text(reason_payload.get("reason"), limit=160)
        else:
            event_type = ""
            reason = clean_lobby_text(reason_payload, limit=160)
        label = " ".join(part for part in (clean_group_id, event_type, reason) if part)
        if _looks_sensitive_operator_diagnostic_text(label):
            continue
        if label:
            labels.append(label)
    return labels


def _safe_health_operation_strings(value: object, *, limit: int) -> list[str]:
    strings = []
    for text in _safe_payload_strings(value, limit=limit):
        if _looks_sensitive_operator_diagnostic_text(text):
            continue
        strings.append(text)
    return strings


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


def _payload_session_smoke_soak_cycle_count(value: object) -> int:
    if value is None or value == "":
        return 0
    try:
        parsed = int(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError("session smoke soak_cycle_count must be between 0 and 5") from error
    if parsed < 0 or parsed > MAX_SESSION_SMOKE_SOAK_CYCLES:
        raise ValueError(f"session smoke soak_cycle_count must be between 0 and {MAX_SESSION_SMOKE_SOAK_CYCLES}")
    return parsed


def _payload_session_smoke_soak_interval_seconds(value: object) -> float:
    if value is None or value == "":
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("session smoke soak_interval_seconds must be between 0 and 60") from error
    if not math.isfinite(parsed) or parsed < 0 or parsed > MAX_SESSION_SMOKE_SOAK_INTERVAL_SECONDS:
        raise ValueError(
            f"session smoke soak_interval_seconds must be between 0 and {MAX_SESSION_SMOKE_SOAK_INTERVAL_SECONDS:g}"
        )
    return parsed


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


def _review_checkpoint_expected_agent_ids(readiness: dict[str, object]) -> list[str]:
    connection = readiness.get("connection") if isinstance(readiness.get("connection"), dict) else {}
    return _safe_payload_strings(connection.get("agent_ids"), limit=64)


def _review_checkpoint_target_agent_ids(value: object, expected_agent_ids: list[str]) -> list[str]:
    if value is None or value == "" or value == []:
        targets = list(expected_agent_ids)
    else:
        if not isinstance(value, list):
            raise ValueError("Review checkpoint agent_ids must be an array.")
        targets = _safe_payload_strings(value, limit=64)
    deduped = list(dict.fromkeys(targets))
    if not deduped:
        raise ValueError("Review checkpoint requires at least one live agent.")
    expected = set(expected_agent_ids)
    unexpected = [agent_id for agent_id in deduped if agent_id not in expected]
    if unexpected:
        raise ValueError(f"Review checkpoint target is not in the ready resident session: {', '.join(unexpected)}.")
    return deduped


def _review_checkpoint_agent_identities(meeting: dict[str, object]) -> dict[str, dict[str, str]]:
    roles = _index_by_id(meeting.get("roles", []))
    identities: dict[str, dict[str, str]] = {}
    for binding in _as_dict_list(meeting.get("agent_bindings", [])):
        agent_id = clean_lobby_text(binding.get("agent_id"), limit=64)
        role_id = clean_lobby_text(binding.get("role_id"), limit=128)
        if not agent_id:
            continue
        role = roles.get(role_id) if role_id else None
        display_name = clean_lobby_text(role.get("display_name"), limit=64) if role else ""
        identities[agent_id] = {
            "role_id": role_id or agent_id,
            "display_name": display_name or agent_id,
        }
    return identities


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


def _review_checkpoint_operation_details(checkpoint: dict[str, object], meeting_id: str) -> dict[str, object]:
    details = _turn_sequence_operation_details(checkpoint, meeting_id)
    details["result_status"] = _operation_result_status(checkpoint.get("status"))
    details["checkpoint_id"] = clean_lobby_text(checkpoint.get("checkpoint_id"), limit=128)
    details["group_id"] = clean_lobby_text(checkpoint.get("group_id"), limit=128)
    reason = clean_lobby_text(checkpoint.get("reason"), limit=128)
    if reason:
        details["reason"] = reason
    expected_agent_ids = _safe_payload_strings(checkpoint.get("expected_agent_ids"), limit=64)
    if expected_agent_ids:
        details["expected_agent_ids"] = expected_agent_ids
    return details


def _review_checkpoint_request_operation_details(payload: dict[str, object], meeting_id: str) -> dict[str, object]:
    return {
        "meeting_id": meeting_id,
        "group_id": clean_live_agent_group_id(str(payload.get("group_id") or "")),
        "checkpoint_id": clean_lobby_text(payload.get("checkpoint_id") or payload.get("review_checkpoint_id"), limit=128),
        "agent_ids": _safe_payload_strings(payload.get("agent_ids"), limit=64),
        "timeout_seconds": _payload_nonnegative_float(payload.get("timeout_seconds", payload.get("timeout")), 30.0),
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
    details = {
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
    finalization = rounds_result.get("finalization") if isinstance(rounds_result.get("finalization"), dict) else None
    if finalization is not None:
        details.update(_rounds_finalization_operation_details(finalization, meeting_id))
    return details


def _rounds_finalization_operation_details(finalization: dict[str, object], meeting_id: str) -> dict[str, object]:
    return {
        "finalization_status": _operation_result_status(finalization.get("status")),
        "finalization_reason": clean_lobby_text(finalization.get("reason"), limit=256),
        "finalization_meeting_id": clean_lobby_text(finalization.get("meeting_id") or meeting_id, limit=128),
        "finalization_official_event_count": _payload_nonnegative_int(finalization.get("official_event_count"), 0),
        "finalization_artifact_event_id": clean_lobby_text(finalization.get("artifact_event_id"), limit=128),
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
        "terminal_session_supported": smoke.get("terminal_session_supported") is True,
        "terminal_session_included": smoke.get("terminal_session_included") is True,
        "terminal_session_status": _operation_result_status(smoke.get("terminal_session_status")),
        "terminal_session_reason": clean_lobby_text(smoke.get("terminal_session_reason"), limit=128),
        "rounds_status": _operation_result_status(smoke.get("rounds_status")),
        "round_count": _payload_nonnegative_int(smoke.get("round_count"), 0),
        "answered_round_count": _payload_nonnegative_int(smoke.get("answered_round_count"), 0),
        "completed_round_count": _payload_nonnegative_int(smoke.get("completed_round_count"), 0),
        "timeout_round_count": _payload_nonnegative_int(smoke.get("timeout_round_count"), 0),
        "skipped_round_count": _payload_nonnegative_int(smoke.get("skipped_round_count"), 0),
        "lobby_probe_count": _payload_nonnegative_int(smoke.get("lobby_probe_count"), 1),
        "expected_reply_count": _payload_nonnegative_int(smoke.get("expected_reply_count"), 0),
        "self_service_official_reply_count": _payload_nonnegative_int(smoke.get("self_service_official_reply_count"), 0),
        "self_service_lobby_reply_count": _payload_nonnegative_int(smoke.get("self_service_lobby_reply_count"), 0),
        "self_service_post_restart_reply_count": _payload_nonnegative_int(
            smoke.get("self_service_post_restart_reply_count"),
            0,
        ),
        "self_service_post_recover_reply_count": _payload_nonnegative_int(
            smoke.get("self_service_post_recover_reply_count"),
            0,
        ),
        "self_service_soak_reply_count": _payload_nonnegative_int(smoke.get("self_service_soak_reply_count"), 0),
        "reply_count": _payload_nonnegative_int(smoke.get("reply_count"), 0),
        "post_restart_reply_count": _payload_nonnegative_int(smoke.get("post_restart_reply_count"), 0),
        "post_recover_reply_count": _payload_nonnegative_int(smoke.get("post_recover_reply_count"), 0),
        "soak_cycle_count": _payload_nonnegative_int(smoke.get("soak_cycle_count"), 0),
        "soak_reply_count": _payload_nonnegative_int(smoke.get("soak_reply_count"), 0),
        "soak_check_statuses": _safe_payload_strings(smoke.get("soak_check_statuses"), limit=32),
        "start_status": _operation_result_status(smoke.get("start_status")),
        "check_status": _operation_result_status(smoke.get("check_status")),
        "resume_status": _operation_result_status(smoke.get("resume_status")),
        "restart_status": _operation_result_status(smoke.get("restart_status")),
        "recover_status": _operation_result_status(smoke.get("recover_status")),
        "stop_status": _operation_result_status(smoke.get("stop_status")),
        "post_stop_process_status": _operation_result_status(smoke.get("post_stop_process_status")),
    }


def _session_smoke_error_details(payload: dict[str, object]) -> dict[str, object]:
    return {
        "group_id": clean_lobby_text(payload.get("group_id"), limit=128),
        "meeting_id": clean_lobby_text(payload.get("meeting_id"), limit=128),
    }


def _session_start_operation_details(session: dict[str, object]) -> dict[str, object]:
    connection = session.get("connection") if isinstance(session.get("connection"), dict) else {}
    process = session.get("process") if isinstance(session.get("process"), dict) else {}
    offline = session.get("offline") if isinstance(session.get("offline"), dict) else {}
    ownership = session.get("ownership") if isinstance(session.get("ownership"), dict) else {}
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
        "ownership_attention": _safe_payload_strings(ownership.get("attention"), limit=128),
    }
    ensure_action = clean_lobby_text(session.get("action"), limit=64)
    if ensure_action:
        details["ensure_action"] = ensure_action
    if offline:
        details.update(
            {
                "offline_agent_count": _payload_nonnegative_int(offline.get("offline"), 0),
                "offline_agent_ids": _safe_payload_strings(offline.get("offline_agent_ids"), limit=64),
                "offline_attention": _safe_payload_strings(offline.get("attention"), limit=128),
            }
        )
    reply_probe = session.get("reply_probe") if isinstance(session.get("reply_probe"), dict) else None
    if reply_probe is not None:
        details.update(_session_reply_probe_operation_details(reply_probe))
    auto_rounds = session.get("auto_rounds") if isinstance(session.get("auto_rounds"), dict) else None
    if auto_rounds is not None:
        details.update(_session_auto_rounds_operation_details(auto_rounds, str(session.get("meeting_id") or "")))
    finalization = session.get("finalization") if isinstance(session.get("finalization"), dict) else None
    if finalization is not None:
        details.update(_rounds_finalization_operation_details(finalization, str(session.get("meeting_id") or "")))
    return details


def _session_stop_operation_details(session: dict[str, object]) -> dict[str, object]:
    offline = session.get("offline") if isinstance(session.get("offline"), dict) else {}
    process = session.get("process") if isinstance(session.get("process"), dict) else {}
    session_runs = session.get("session_runs") if isinstance(session.get("session_runs"), list) else []
    stopped_session_run_ids = [
        clean_lobby_text(run.get("run_id"), limit=64)
        for run in session_runs
        if isinstance(run, dict) and run.get("status") == "stopped" and clean_lobby_text(run.get("run_id"), limit=64)
    ]
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
        "session_run_stopped_count": len(stopped_session_run_ids),
        "session_run_ids": stopped_session_run_ids[:10],
    }


def _meeting_finalize_operation_details(result: dict[str, object], meeting_id: str) -> dict[str, object]:
    return {
        "result_status": _operation_result_status(result.get("status")),
        "meeting_id": clean_lobby_text(result.get("meeting_id") or meeting_id, limit=128),
        "official_event_count": _payload_nonnegative_int(result.get("official_event_count"), 0),
        "artifact_event_id": clean_lobby_text(result.get("artifact_event_id"), limit=128),
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


def _session_reply_probe_operation_details(reply_probe: dict[str, object]) -> dict[str, object]:
    return {
        "reply_probe_status": _operation_result_status(reply_probe.get("status")),
        "reply_probe_reason": clean_lobby_text(reply_probe.get("reason"), limit=128),
        "reply_probe_agent_ids": _safe_payload_strings(reply_probe.get("agent_ids"), limit=64),
        "reply_probe_statuses": _probe_statuses(reply_probe.get("probes")),
        "reply_probe_count": _payload_nonnegative_int(reply_probe.get("probe_count"), 0),
        "reply_probe_ok_count": _payload_nonnegative_int(reply_probe.get("ok_count"), 0),
        "reply_probe_timeout_count": _payload_nonnegative_int(reply_probe.get("timeout_count"), 0),
        "reply_probe_failed_count": _payload_nonnegative_int(reply_probe.get("failed_count"), 0),
        "reply_probe_skipped_count": _payload_nonnegative_int(reply_probe.get("skipped_count"), 0),
        "reply_probe_timeout_seconds": _payload_nonnegative_float(reply_probe.get("timeout_seconds"), 0.0),
    }


def _session_start_operation_status(session: dict[str, object]) -> str:
    if _operation_result_status(session.get("status")) != "ready":
        return "degraded"
    reply_probe = session.get("reply_probe") if isinstance(session.get("reply_probe"), dict) else None
    if reply_probe is not None and _operation_result_status(reply_probe.get("status")) != "ok":
        return "degraded"
    auto_rounds = session.get("auto_rounds") if isinstance(session.get("auto_rounds"), dict) else None
    if auto_rounds is not None and _operation_result_status(auto_rounds.get("status")) not in {"answered", "complete"}:
        return "degraded"
    finalization = session.get("finalization") if isinstance(session.get("finalization"), dict) else None
    if finalization is not None and _operation_result_status(finalization.get("status")) not in {"finalized", "already_finalized"}:
        return "degraded"
    return "success"


def _session_run_retry_now_operation_status(session_run: dict[str, object], *, reconciled: bool) -> str:
    if not reconciled:
        return "success"
    status = _operation_result_status(session_run.get("status"))
    if status in {"failed", "stopped"}:
        return "failed"
    return "success" if status == "ready" else "degraded"


def _session_start_operation_summary(session: dict[str, object]) -> str:
    if _operation_result_status(session.get("status")) != "ready":
        return "resident live-agent session is still connecting"
    reply_probe = session.get("reply_probe") if isinstance(session.get("reply_probe"), dict) else None
    if reply_probe is not None and _operation_result_status(reply_probe.get("status")) != "ok":
        return "started resident live-agent session with degraded reply probe"
    auto_rounds = session.get("auto_rounds") if isinstance(session.get("auto_rounds"), dict) else None
    if auto_rounds is None:
        return "started resident live-agent session"
    if _operation_result_status(auto_rounds.get("status")) in {"answered", "complete"}:
        return "started resident live-agent session and ran remaining rounds"
    return "started resident live-agent session with degraded remaining rounds"


def _session_resume_operation_summary(session: dict[str, object]) -> str:
    if _operation_result_status(session.get("status")) != "ready":
        return "resident live-agent session is still reconnecting"
    reply_probe = session.get("reply_probe") if isinstance(session.get("reply_probe"), dict) else None
    if reply_probe is not None and _operation_result_status(reply_probe.get("status")) != "ok":
        return "resumed resident live-agent session with degraded reply probe"
    auto_rounds = session.get("auto_rounds") if isinstance(session.get("auto_rounds"), dict) else None
    if auto_rounds is None:
        return "resumed resident live-agent session"
    if _operation_result_status(auto_rounds.get("status")) in {"answered", "complete"}:
        return "resumed resident live-agent session and ran remaining rounds"
    return "resumed resident live-agent session with degraded remaining rounds"


def _session_ensure_operation_summary(session: dict[str, object]) -> str:
    action = clean_lobby_text(session.get("action"), limit=64) or "unknown"
    if action == "none":
        return "resident live-agent session already ready"
    if _operation_result_status(session.get("status")) != "ready":
        return f"resident live-agent session ensure still connecting via {action}"
    reply_probe = session.get("reply_probe") if isinstance(session.get("reply_probe"), dict) else None
    if reply_probe is not None and _operation_result_status(reply_probe.get("status")) != "ok":
        return f"ensured resident live-agent session via {action} with degraded reply probe"
    auto_rounds = session.get("auto_rounds") if isinstance(session.get("auto_rounds"), dict) else None
    if auto_rounds is None:
        return f"ensured resident live-agent session via {action}"
    if _operation_result_status(auto_rounds.get("status")) in {"answered", "complete"}:
        return f"ensured resident live-agent session via {action} and ran remaining rounds"
    return f"ensured resident live-agent session via {action} with degraded remaining rounds"


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
        reply_probe = session.get("reply_probe") if isinstance(session.get("reply_probe"), dict) else None
        if reply_probe is not None and _operation_result_status(reply_probe.get("status")) != "ok":
            return "restarted resident live-agent session with degraded reply probe"
        auto_rounds = session.get("auto_rounds") if isinstance(session.get("auto_rounds"), dict) else None
        if auto_rounds is None:
            return "restarted resident live-agent session"
        if _operation_result_status(auto_rounds.get("status")) in {"answered", "complete"}:
            return "restarted resident live-agent session and ran remaining rounds"
        return "restarted resident live-agent session with degraded remaining rounds"
    return "resident live-agent session is still reconnecting after restart"


def _session_recover_operation_summary(session: dict[str, object]) -> str:
    if _operation_result_status(session.get("status")) == "ready":
        reply_probe = session.get("reply_probe") if isinstance(session.get("reply_probe"), dict) else None
        if reply_probe is not None and _operation_result_status(reply_probe.get("status")) != "ok":
            return "recovered resident live-agent session with degraded reply probe"
        auto_rounds = session.get("auto_rounds") if isinstance(session.get("auto_rounds"), dict) else None
        if auto_rounds is None:
            return "recovered resident live-agent session"
        if _operation_result_status(auto_rounds.get("status")) in {"answered", "complete"}:
            return "recovered resident live-agent session and ran remaining rounds"
        return "recovered resident live-agent session with degraded remaining rounds"
    return "resident live-agent session is still reconnecting after recovery"


def _session_start_error_message(error: Exception) -> str:
    return _session_error_message(error, action="start")


def _session_resume_error_message(error: Exception) -> str:
    return _session_error_message(error, action="resume")


def _session_ensure_error_message(error: Exception) -> str:
    return _session_error_message(error, action="ensure")


def _session_restart_error_message(error: Exception) -> str:
    return _session_error_message(error, action="restart")


def _session_recover_error_message(error: Exception) -> str:
    return _session_error_message(error, action="recover")


def _session_check_error_message(error: Exception) -> str:
    return _session_error_message(error, action="check")


def _session_stop_error_message(error: Exception) -> str:
    return _session_error_message(error, action="stop")


def _process_start_error_message(error: Exception) -> str:
    return _process_control_error_message(error, action="start")


def _process_stop_error_message(error: Exception) -> str:
    return _process_control_error_message(error, action="stop")


def _process_restart_error_message(error: Exception) -> str:
    return _process_control_error_message(error, action="restart")


def _process_recover_error_message(error: Exception) -> str:
    return _process_control_error_message(error, action="recover")


def _process_stop_running_error_message(error: Exception) -> str:
    return _process_control_error_message(error, action="stop running groups")


def _safe_diagnostic_report_payload(report: dict[str, object]) -> dict[str, object]:
    safe = dict(report)
    has_failed_config_load = _diagnostic_report_has_failed_config_load(safe)
    if has_failed_config_load or _diagnostic_report_exposes_sensitive_config_path(safe):
        safe["config_path"] = "[redacted]"
    checks = safe.get("checks")
    if isinstance(checks, list):
        safe["checks"] = [
            _safe_diagnostic_check_payload(check, redact_config_load=has_failed_config_load)
            for check in checks
        ]
    return safe


def _diagnostic_report_has_failed_config_load(report: dict[str, object]) -> bool:
    checks = report.get("checks")
    if not isinstance(checks, list):
        return False
    return any(
        isinstance(check, dict) and check.get("id") == "config_load" and check.get("status") == "failed"
        for check in checks
    )


def _diagnostic_report_exposes_sensitive_config_path(report: dict[str, object]) -> bool:
    if report.get("status") != "failed":
        return False
    config_path = str(report.get("config_path") or "")
    return bool(config_path and _looks_sensitive_operator_diagnostic_text(config_path))


def _safe_diagnostic_check_payload(check: object, *, redact_config_load: bool) -> object:
    if not isinstance(check, dict):
        return check
    safe = dict(check)
    message = str(safe.get("message") or "")
    if (
        redact_config_load
        and safe.get("id") == "config_load"
        and safe.get("status") == "failed"
    ) or _looks_sensitive_operator_diagnostic_text(message):
        safe["message"] = "Config load failed: details redacted."
    return safe


def _looks_sensitive_operator_diagnostic_text(message: str) -> bool:
    return _looks_sensitive_process_control_error(message)


def _process_control_error_message(error: Exception, *, action: str) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ").strip()
    fallback = f"Resident process group failed to {action}."
    if _looks_sensitive_process_control_error(message):
        return f"Resident process group failed to {action}: details redacted."
    return message[:500] or fallback


def _looks_sensitive_process_control_error(message: str) -> bool:
    lowered = message.casefold()
    markers = (
        "authorization",
        "bearer ",
        "secret",
        "token",
        "api-key",
        "apikey",
        "x-api-key",
        "password",
        "http://",
        "https://",
        "env:",
        ".json",
        ".env",
        ".toml",
    )
    if any(marker in lowered for marker in markers):
        return True
    if "\\" in message or "--" in message:
        return True
    if re.search(r"(^|[\s=])(?:/|~/|\./|\.\./)\S+", message):
        return True
    return bool(re.search(r"\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)", message))


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
    session_run_controller: LiveAgentSessionRunController | None = None,
) -> type[BaseHTTPRequestHandler]:
    static_root = Path(__file__).parent / "static"
    live_agent_process_supervisor = process_supervisor or LiveAgentProcessSupervisor(output_root)
    live_agent_session_run_controller = session_run_controller or LiveAgentSessionRunController(output_root)

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
            if path == "/api/live-agent-sessions/readiness":
                try:
                    self._send_json(
                        live_agent_session_readiness_payload(
                            output_root,
                            live_agent_process_supervisor,
                            meeting_id=str(query.get("meeting_id", [""])[0] or ""),
                            group_id=str(query.get("group_id", [""])[0] or ""),
                        )
                    )
                except (OSError, ValueError) as error:
                    details = {
                        "requested_meeting_id": str(query.get("meeting_id", [""])[0] or ""),
                        "group_id": str(query.get("group_id", [""])[0] or ""),
                    }
                    self._send_error(HTTPStatus.BAD_REQUEST, _session_check_error_message(error), details=details)
                return
            if path == "/api/live-agent-processes":
                self._send_json(live_agent_processes_payload(live_agent_process_supervisor, output_root=output_root))
                return
            if path == "/api/live-agent-process-events":
                self._send_json(
                    live_agent_process_events_payload(
                        output_root,
                        limit=self._limit(query, default=50),
                        group_id=str(query.get("group_id", [""])[0] or ""),
                        scan_limit=query.get("scan_limit", [""])[0],
                    )
                )
                return
            if path == "/api/live-agent-operations":
                self._send_json(
                    live_agent_operations_payload(
                        output_root,
                        limit=self._limit(query, default=50),
                        operation=str(query.get("operation", [""])[0] or ""),
                        target_id=str(query.get("target_id", [""])[0] or ""),
                        status=str(query.get("status", [""])[0] or ""),
                        scan_limit=query.get("scan_limit", [""])[0],
                        scan_tail=_payload_bool(query.get("scan_tail", [""])[0]),
                    )
                )
                return
            if path == "/api/live-agent-session-runs":
                self._send_json(
                    live_agent_session_runs_payload(
                        live_agent_session_run_controller,
                        limit=self._limit(query, default=50),
                        meeting_id=str(query.get("meeting_id", [""])[0] or ""),
                        group_id=str(query.get("group_id", [""])[0] or ""),
                        include_readiness=_payload_bool(query.get("include_readiness", [""])[0]),
                        output_root=output_root,
                        process_supervisor=live_agent_process_supervisor,
                    )
                )
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
            session_run_retry_now_id = _live_agent_session_run_action_path(parsed.path, "retry-now")
            if session_run_retry_now_id is not None or parsed.path == "/api/live-agent-session-runs/retry-now":
                payload = self._operation_json_payload(
                    operation="session_run.retry_now",
                    target_id=session_run_retry_now_id or "",
                )
                if payload is None:
                    return
                run_id = session_run_retry_now_id or str(payload.get("run_id") or "").strip()
                if not run_id:
                    record_live_agent_operation(
                        output_root,
                        operation="session_run.retry_now",
                        status="failed",
                        error="Missing session run id",
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, "Missing session run id")
                    return
                try:
                    current_run = live_agent_session_run_controller.get_run(run_id)
                    if not _session_run_monitor_should_reconcile(
                        output_root,
                        live_agent_process_supervisor,
                        current_run,
                        target_run_id=str(current_run.get("run_id") or run_id),
                    ):
                        record_live_agent_operation(
                            output_root,
                            operation="session_run.retry_now",
                            status="success",
                            target_id=str(current_run.get("run_id") or run_id),
                            summary="skipped durable live-agent session-run retry because it is already ready",
                            details={
                                "session_run_id": str(current_run.get("run_id") or run_id),
                                "meeting_id": str(current_run.get("meeting_id") or ""),
                                "group_id": str(current_run.get("group_id") or ""),
                                "run_status": str(current_run.get("status") or ""),
                                "phase": str(current_run.get("phase") or ""),
                                "reconciled": False,
                                "result_count": 0,
                                "skipped_reason": "already_ready",
                            },
                        )
                        self._send_json({"status": "skipped", "session_run": current_run, "results": []})
                        return
                    scheduled_run = live_agent_session_run_controller.retry_run_now(run_id)
                    results = _reconcile_live_agent_session_runs(
                        output_root,
                        live_agent_process_supervisor,
                        live_agent_session_run_controller,
                        default_server=self._request_server_url(),
                        summary="retried durable live-agent session run immediately",
                        target_run_id=str(scheduled_run.get("run_id") or run_id),
                    )
                except (OSError, ValueError) as error:
                    safe_error = _session_ensure_error_message(error)
                    record_live_agent_operation(
                        output_root,
                        operation="session_run.retry_now",
                        status="failed",
                        target_id=run_id,
                        error=safe_error,
                        details={"session_run_id": run_id},
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, safe_error, details={"session_run_id": run_id})
                    return
                session_run = results[-1] if results else scheduled_run
                response_status = "reconciled" if results else "scheduled"
                operation_status = _session_run_retry_now_operation_status(session_run, reconciled=bool(results))
                record_live_agent_operation(
                    output_root,
                    operation="session_run.retry_now",
                    status=operation_status,
                    target_id=str(session_run.get("run_id") or run_id),
                    summary="scheduled immediate durable live-agent session-run retry",
                    details={
                        "session_run_id": str(session_run.get("run_id") or run_id),
                        "meeting_id": str(session_run.get("meeting_id") or ""),
                        "group_id": str(session_run.get("group_id") or ""),
                        "run_status": str(session_run.get("status") or ""),
                        "phase": str(session_run.get("phase") or ""),
                        "reconciled": bool(results),
                        "result_count": len(results),
                    },
                )
                self._send_json({"status": response_status, "session_run": session_run, "results": results})
                return
            if parsed.path == "/api/live-agent-session-runs/ensure":
                payload = self._operation_json_payload(operation="session_run.ensure")
                if payload is None:
                    return
                session_run = live_agent_session_run_controller.begin_run(action="ensure", payload=dict(payload))
                try:
                    session = live_agent_session_ensure_payload(
                        output_root,
                        live_agent_process_supervisor,
                        payload,
                        default_server=self._request_server_url(),
                    )
                except (OSError, ValueError) as error:
                    safe_error = _session_ensure_error_message(error)
                    failed_run = live_agent_session_run_controller.fail_run(session_run["run_id"], safe_error)
                    safe_details = _session_start_error_details(payload, error)
                    safe_details["session_run_id"] = str(failed_run.get("run_id") or "")
                    record_live_agent_operation(
                        output_root,
                        operation="session_run.ensure",
                        status="failed",
                        target_id=str(safe_details.get("meeting_id") or safe_details.get("requested_meeting_id") or ""),
                        error=safe_error,
                        details=safe_details,
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, safe_error, details=safe_details)
                    return
                finished_run = live_agent_session_run_controller.finish_run(session_run["run_id"], session=session)
                session["session_run"] = finished_run
                record_live_agent_operation(
                    output_root,
                    operation="session_run.ensure",
                    status=_session_start_operation_status(session),
                    target_id=str(session.get("meeting_id") or payload.get("meeting_id") or ""),
                    summary="ensured durable live-agent session run",
                    details={
                        **_session_start_operation_details(session),
                        "session_run_id": str(finished_run.get("run_id") or ""),
                    },
                )
                self._send_json(session)
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
            if parsed.path == "/api/live-agent-sessions/ensure":
                payload = self._operation_json_payload(operation="session.ensure")
                if payload is None:
                    return
                try:
                    session = live_agent_session_ensure_payload(
                        output_root,
                        live_agent_process_supervisor,
                        payload,
                        default_server=self._request_server_url(),
                    )
                except (OSError, ValueError) as error:
                    safe_error = _session_ensure_error_message(error)
                    safe_details = _session_start_error_details(payload, error)
                    record_live_agent_operation(
                        output_root,
                        operation="session.ensure",
                        status="failed",
                        target_id=str(safe_details.get("meeting_id") or safe_details.get("requested_meeting_id") or ""),
                        error=safe_error,
                        details=safe_details,
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, safe_error, details=safe_details)
                    return
                record_live_agent_operation(
                    output_root,
                    operation="session.ensure",
                    status=_session_start_operation_status(session),
                    target_id=str(session.get("meeting_id") or payload.get("meeting_id") or ""),
                    summary=_session_ensure_operation_summary(session),
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
            if parsed.path == "/api/live-agent-sessions/recover":
                payload = self._operation_json_payload(operation="session.recover")
                if payload is None:
                    return
                try:
                    session = live_agent_session_recover_payload(
                        output_root,
                        live_agent_process_supervisor,
                        payload,
                    )
                except (OSError, ValueError) as error:
                    safe_error = _session_recover_error_message(error)
                    safe_details = _session_start_error_details(payload, error)
                    record_live_agent_operation(
                        output_root,
                        operation="session.recover",
                        status="failed",
                        target_id=str(safe_details.get("meeting_id") or safe_details.get("requested_meeting_id") or ""),
                        error=safe_error,
                        details=safe_details,
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, safe_error, details=safe_details)
                    return
                record_live_agent_operation(
                    output_root,
                    operation="session.recover",
                    status=_session_start_operation_status(session),
                    target_id=str(session.get("meeting_id") or payload.get("meeting_id") or ""),
                    summary=_session_recover_operation_summary(session),
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
                stopped_runs = live_agent_session_run_controller.mark_matching_stopped(
                    meeting_id=str(session.get("meeting_id") or payload.get("meeting_id") or ""),
                    group_id=str(session.get("group_id") or payload.get("group_id") or ""),
                    reason="session.stop",
                )
                if stopped_runs:
                    session["session_runs"] = stopped_runs
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
            finalize_meeting_id = _meeting_finalize_path(parsed.path)
            if finalize_meeting_id is not None:
                payload = self._operation_json_payload(
                    operation="meeting.finalize",
                    target_id=finalize_meeting_id,
                    details={"meeting_id": clean_lobby_text(finalize_meeting_id, limit=128)},
                )
                if payload is None:
                    return
                try:
                    finalized = live_agent_finalize_meeting_payload(output_root, finalize_meeting_id, payload)
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    record_live_agent_operation(
                        output_root,
                        operation="meeting.finalize",
                        status="failed",
                        target_id=finalize_meeting_id,
                        error=str(error),
                        details={"meeting_id": clean_lobby_text(finalize_meeting_id, limit=128)},
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                record_live_agent_operation(
                    output_root,
                    operation="meeting.finalize",
                    status="success" if finalized.get("status") in {"finalized", "already_finalized"} else "degraded",
                    target_id=finalize_meeting_id,
                    summary="finalized resident live-agent meeting artifacts",
                    details=_meeting_finalize_operation_details(finalized, finalize_meeting_id),
                )
                self._send_json(finalized)
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
            review_checkpoint_meeting_id = _meeting_review_checkpoint_path(parsed.path)
            if review_checkpoint_meeting_id is not None:
                payload = self._operation_json_payload(operation="review.checkpoint")
                if payload is None:
                    return
                try:
                    checkpoint = live_agent_review_checkpoint_payload(
                        output_root,
                        live_agent_process_supervisor,
                        review_checkpoint_meeting_id,
                        payload,
                    )
                except ValueError as error:
                    record_live_agent_operation(
                        output_root,
                        operation="review.checkpoint",
                        status="failed",
                        target_id=review_checkpoint_meeting_id,
                        error=str(error),
                        details=_review_checkpoint_request_operation_details(payload, review_checkpoint_meeting_id),
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                checkpoint_status = str(checkpoint.get("status") or "unknown")
                record_live_agent_operation(
                    output_root,
                    operation="review.checkpoint",
                    status="success" if checkpoint_status == "answered" else "degraded",
                    target_id=review_checkpoint_meeting_id,
                    summary=(
                        "completed resident live-agent review checkpoint"
                        if checkpoint_status == "answered"
                        else "resident live-agent review checkpoint was not fully answered"
                    ),
                    details=_review_checkpoint_operation_details(checkpoint, review_checkpoint_meeting_id),
                )
                self._send_json(checkpoint)
                return
            turn_rounds_meeting_id = _meeting_live_agent_turn_rounds_path(parsed.path)
            if turn_rounds_meeting_id is not None:
                payload = self._operation_json_payload(operation="official_turn.rounds")
                if payload is None:
                    return
                try:
                    rounds_result = live_agent_turn_rounds_payload(output_root, turn_rounds_meeting_id, payload)
                    finalization = _rounds_finalization_result_if_requested(
                        output_root,
                        turn_rounds_meeting_id,
                        rounds_result,
                        payload,
                    )
                    if finalization is not None:
                        rounds_result["finalization"] = finalization
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
                finalization_result = rounds_result.get("finalization") if isinstance(rounds_result.get("finalization"), dict) else None
                rounds_success = rounds_result.get("status") in {"answered", "complete"}
                finalization_success = (
                    finalization_result is None
                    or finalization_result.get("status") in {"finalized", "already_finalized"}
                )
                record_live_agent_operation(
                    output_root,
                    operation="official_turn.rounds",
                    status="success" if rounds_success and finalization_success else "degraded",
                    target_id=turn_rounds_meeting_id,
                    summary=(
                        "completed live-agent remaining official rounds"
                        if rounds_success and finalization_success
                        else "completed live-agent remaining official rounds with degraded finalization"
                        if rounds_success
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
                    self._send_error(
                        HTTPStatus.BAD_REQUEST,
                        _process_start_error_message(error),
                        details={"group_id": _operation_group_id(payload)},
                    )
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
            if parsed.path == "/api/live-agent-processes/stop-running":
                payload = self._operation_json_payload(operation="process.stop_running", target_id="running-groups")
                if payload is None:
                    return
                try:
                    stopped = stop_running_live_agent_processes_payload(
                        live_agent_process_supervisor,
                        output_root=output_root,
                    )
                except ValueError as error:
                    record_live_agent_operation(
                        output_root,
                        operation="process.stop_running",
                        status="failed",
                        target_id="running-groups",
                        error=str(error),
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, _process_stop_running_error_message(error))
                    return
                result = stopped.get("result") if isinstance(stopped.get("result"), dict) else {}
                record_live_agent_operation(
                    output_root,
                    operation="process.stop_running",
                    status=_process_stop_running_operation_status(result),
                    target_id="running-groups",
                    summary="stopped running live-agent process groups",
                    details={
                        "stopped_count": _payload_nonnegative_int(result.get("stopped_count"), 0),
                        "failed_count": _payload_nonnegative_int(result.get("failed_count"), 0),
                        "skipped_count": _payload_nonnegative_int(result.get("skipped_count"), 0),
                        "stopped_group_ids": _operation_group_ids(result.get("stopped")),
                        "failed_group_ids": _operation_group_ids(result.get("failed")),
                        **_process_bulk_offline_operation_details(result.get("stopped")),
                    },
                )
                self._send_json(stopped)
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
                self._send_json(_safe_diagnostic_report_payload(preflight))
                return
            if parsed.path == "/api/live-agent-discovery":
                payload = self._operation_json_payload(operation="discovery.run")
                if payload is None:
                    return
                discovery = live_agent_discovery_payload(output_root, payload, default_server=self._request_server_url())
                result_status = _operation_result_status(discovery.get("status"))
                discoveries = discovery.get("discoveries") if isinstance(discovery.get("discoveries"), list) else []
                agents = (discovery.get("config") or {}).get("agents", []) if isinstance(discovery.get("config"), dict) else []
                record_live_agent_operation(
                    output_root,
                    operation="discovery.run",
                    status=_operation_success_for_result(result_status, success_values={"ok"}),
                    target_id="live-agent-discovery",
                    summary="discovered local live-agent CLIs",
                    details={
                        "result_status": result_status,
                        "agents": len(agents) if isinstance(agents, list) else 0,
                        "discovered": sum(1 for item in discoveries if isinstance(item, dict) and item.get("available")),
                    },
                )
                self._send_json(discovery)
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
                    self._send_json(_safe_diagnostic_report_payload(provider_health_payload(payload)))
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
                session_smoke = readiness.get("session_smoke") if isinstance(readiness.get("session_smoke"), dict) else {}
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
                        **_readiness_health_operation_details(readiness.get("health")),
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
                        "session_smoke": _operation_result_status(session_smoke.get("status")),
                        "session_smoke_terminal_session_status": _operation_result_status(
                            session_smoke.get("terminal_session_status")
                        ),
                        "session_smoke_terminal_session_included": session_smoke.get("terminal_session_included") is True,
                        "session_smoke_self_service_official_reply_count": _payload_nonnegative_int(
                            session_smoke.get("self_service_official_reply_count"),
                            0,
                        ),
                        "session_smoke_self_service_lobby_reply_count": _payload_nonnegative_int(
                            session_smoke.get("self_service_lobby_reply_count"),
                            0,
                        ),
                        "session_smoke_self_service_post_restart_reply_count": _payload_nonnegative_int(
                            session_smoke.get("self_service_post_restart_reply_count"),
                            0,
                        ),
                        "session_smoke_self_service_post_recover_reply_count": _payload_nonnegative_int(
                            session_smoke.get("self_service_post_recover_reply_count"),
                            0,
                        ),
                        "session_smoke_self_service_soak_reply_count": _payload_nonnegative_int(
                            session_smoke.get("self_service_soak_reply_count"),
                            0,
                        ),
                        "session_smoke_reply_count": _payload_nonnegative_int(session_smoke.get("reply_count"), 0),
                        "session_smoke_post_restart_reply_count": _payload_nonnegative_int(
                            session_smoke.get("post_restart_reply_count"),
                            0,
                        ),
                        "session_smoke_post_recover_reply_count": _payload_nonnegative_int(
                            session_smoke.get("post_recover_reply_count"),
                            0,
                        ),
                        "session_smoke_soak_cycle_count": _payload_nonnegative_int(
                            session_smoke.get("soak_cycle_count"),
                            0,
                        ),
                        "session_smoke_soak_reply_count": _payload_nonnegative_int(
                            session_smoke.get("soak_reply_count"),
                            0,
                        ),
                        "session_smoke_soak_check_statuses": _safe_payload_strings(
                            session_smoke.get("soak_check_statuses"),
                            limit=32,
                        ),
                        "session_smoke_post_stop_process_status": _operation_result_status(
                            session_smoke.get("post_stop_process_status")
                        ),
                        "session_smoke_recover_status": _operation_result_status(session_smoke.get("recover_status")),
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
                    self._send_error(
                        HTTPStatus.BAD_REQUEST,
                        _process_stop_error_message(error),
                        details={"group_id": live_agent_process_stop_id},
                    )
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
                        **_process_offline_operation_details(group.get("offline")),
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
                    self._send_error(
                        HTTPStatus.BAD_REQUEST,
                        _process_restart_error_message(error),
                        details={"group_id": live_agent_process_restart_id},
                    )
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
                    self._send_error(
                        HTTPStatus.BAD_REQUEST,
                        _process_recover_error_message(error),
                        details={"group_id": live_agent_process_recover_id},
                    )
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
                review_checkpoint_id = clean_lobby_text(event.get("review_checkpoint_id"), limit=128)
                reply_operation = "review.reply" if review_checkpoint_id else "official_turn.reply"
                reply_details = {
                    "meeting_id": str(event.get("meeting_id") or payload.get("meeting_id") or ""),
                    "source_event_id": str(event.get("source_event_id") or ""),
                    "role_id": str(event.get("role_id") or ""),
                    "turn_id": str(event.get("turn_id") or ""),
                    "turn_index": _payload_optional_int(event.get("turn_index")),
                }
                if review_checkpoint_id:
                    reply_details["review_checkpoint_id"] = review_checkpoint_id
                record_live_agent_operation(
                    output_root,
                    operation=reply_operation,
                    status="success",
                    target_id=live_agent_official_turn_id,
                    summary=(
                        "recorded live-agent review checkpoint reply"
                        if review_checkpoint_id
                        else "recorded live-agent official turn"
                    ),
                    details=reply_details,
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
                except ValueError:
                    safe_error = "Codex live session invite failed."
                    safe_details = _codex_session_invite_error_details(output_root, payload)
                    record_live_agent_operation(
                        output_root,
                        operation="codex_session.invite",
                        status="failed",
                        target_id=safe_details.get("role_id", ""),
                        summary="Codex live session invite failed",
                        error=safe_error,
                        details=safe_details,
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, safe_error, details=safe_details)
                    return
                operation_details = _codex_session_invite_operation_details(invite)
                record_live_agent_operation(
                    output_root,
                    operation="codex_session.invite",
                    status="success",
                    target_id=operation_details.get("role_id", ""),
                    summary="wrote Codex live session invite",
                    details=operation_details,
                )
                self._send_json(invite)
                return
            if parsed.path == "/api/codex-sessions/join":
                payload = self._operation_json_payload(operation="codex_session.join")
                if payload is None:
                    return
                try:
                    join = codex_session_join_payload(
                        output_root,
                        live_agent_process_supervisor,
                        payload,
                        default_server=self._request_server_url(),
                    )
                except (OSError, ValueError):
                    safe_error = "Codex live session join failed."
                    safe_details = _codex_session_join_error_details(output_root, payload)
                    record_live_agent_operation(
                        output_root,
                        operation="codex_session.join",
                        status="failed",
                        target_id=str(safe_details.get("role_id") or safe_details.get("meeting_id") or ""),
                        summary="Codex live session join failed",
                        error=safe_error,
                        details=safe_details,
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, safe_error, details=safe_details)
                    return
                operation_details = _codex_session_join_operation_details(join)
                record_live_agent_operation(
                    output_root,
                    operation="codex_session.join",
                    status=_session_start_operation_status(join),
                    target_id=str(operation_details.get("role_id") or join.get("meeting_id") or ""),
                    summary="joined Codex live session",
                    details=operation_details,
                )
                self._send_json(join)
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
