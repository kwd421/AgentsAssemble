from __future__ import annotations

import json
import math
import mimetypes
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from uuid import NAMESPACE_URL, uuid4, uuid5

from agentsassemble.adapters.remote_bridge import RemoteBridgeAdapter
from agentsassemble.attachments import (
    AttachmentError,
    INLINE_SAFE_IMAGE_TYPES,
    attachment_content_disposition,
    normalize_attachment_references,
    read_attachment_file,
    store_uploaded_attachment,
)
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
    apply_discovery_approval_filter,
    build_discovered_live_agent_config,
    build_discovered_session_bundle,
    discovered_session_bundle_paths,
    fill_discovery_next_command_output,
    validate_distinct_session_bundle_paths,
)
from agentsassemble.live_agent_context import live_agent_context_contract, live_agent_context_contract_with_join_semantics
from agentsassemble.live_agent_flow import FLOW_SPEAKING_ACTIONS, FLOW_TERMINAL_EVENT_TYPES, FlowOptions, flow_turn_count
from agentsassemble.live_agent_frontend_create import (
    ensure_frontend_meeting,
    frontend_live_agent_check_payload,
    frontend_live_agent_create_payload,
    frontend_live_agent_login_payload,
    frontend_live_agent_options_payload,
)
from agentsassemble.gui_room_http import register_room_routes
from agentsassemble.gui_router import GuiDeps, RequestContext, Router
from agentsassemble.live_agent_join_brief import build_live_agent_join_brief
from agentsassemble.live_agent_room_admin import (
    delete_live_agent_session_payload,
    expel_live_agent_from_room_payload,
)
from agentsassemble.live_agent_timing import DEFAULT_LIVE_AGENT_POLL_INTERVAL
from agentsassemble.live_agent_launch_policy import APPROVAL_REQUIRED_MESSAGE, assert_resident_launch_approved
from agentsassemble.live_agent_preflight import preflight_live_agent_config
from agentsassemble.live_agent_quota import LIVE_AGENT_QUOTA_FIELDS, quota_viewer_for_host, quota_viewer_for_session
from agentsassemble.live_agent_runner import load_group_configs
from agentsassemble.live_agent_roster import filter_live_agent_roster, safe_live_agent_roster_payload
from agentsassemble.live_agent_settings import update_live_agent_config_poll_interval
from agentsassemble.live_agents import (
    connect_live_agent,
    heartbeat_live_agent,
    read_live_agents,
    update_live_agent_cooldown,
    update_live_agent_engagement,
    update_live_agent_poll_interval,
)
from agentsassemble.live_agent_operations import append_live_agent_operation, read_live_agent_operation_history
from agentsassemble.lobby_promotion import LOBBY_PROMOTION_OPERATION, promote_lobby_events_to_official
from agentsassemble.live_agent_meetings import start_live_agent_meeting
from agentsassemble.live_agent_finalization import finalize_live_agent_meeting
from agentsassemble.live_agent_processes import (
    LiveAgentProcessSupervisor,
    clean_live_agent_group_id,
    read_live_agent_process_event_history,
)
from agentsassemble.local_resources import cached_local_resource_snapshot
from agentsassemble.live_agent_probe import PROBE_REPLY_EVENT_TAIL_LIMIT, run_live_agent_probe, safe_probe_timeout
from agentsassemble.live_agent_play_presets import build_play_preset_turns
from agentsassemble.live_agent_review_checkpoints import write_review_checkpoint_artifacts
from agentsassemble.live_agent_rounds import build_official_round_turns, completed_official_round_ids, remaining_official_round_ids
from agentsassemble.live_agent_sessions import (
    check_live_agent_session,
    live_agent_session_readiness_summary,
    recover_live_agent_session,
    restart_live_agent_session,
    resume_live_agent_session_agent,
    resume_live_agent_session,
    session_ensure_action,
    start_live_agent_session,
    stop_live_agent_session_agent,
    stop_live_agent_session,
)
from agentsassemble.live_agent_session_runs import LiveAgentSessionRunController
from agentsassemble.live_agent_smoke import (
    LiveAgentSmokeFailed,
    MAX_SESSION_SMOKE_SOAK_CYCLES,
    MAX_SESSION_SMOKE_SOAK_INTERVAL_SECONDS,
    run_live_agent_official_round_smoke,
    run_live_agent_real_session_smoke,
    run_live_agent_session_smoke,
    run_live_agent_smoke,
)
from agentsassemble.live_agent_turns import (
    is_official_turn_cancellation_event,
    is_official_turn_reply_event,
    is_review_checkpoint_reply_event,
    official_turn_cancellation,
    wait_for_official_turn_reply,
    wait_for_review_checkpoint_reply,
)
from agentsassemble.live_meeting_memory import (
    build_live_meeting_memory,
    load_live_meeting_memory_context,
    projected_live_meeting_memory_artifacts,
    write_live_meeting_memory_artifacts,
)
from agentsassemble.live_transcript import projected_live_transcript_text
from agentsassemble.mafia_game import (
    cast_mafia_vote,
    mafia_game_payload,
    post_mafia_chat,
    resolve_mafia_phase,
    start_mafia_game,
    submit_mafia_action,
)
from agentsassemble.meeting import run_demo_meeting
from agentsassemble.meeting_lifecycle import infer_live_status, project_meeting_lifecycle
from agentsassemble.provider_health import provider_health_report
from agentsassemble.public_tunnel import PublicTunnelManager
from agentsassemble.frontend_runtime import (
    REACT_APP_BUILD_COMMAND,
    REACT_APP_MISSING_BUILD_MESSAGE,
    default_frontend_dist_root,
    frontend_dist_status,
)
from agentsassemble.release_health import release_health_catalog_payload, release_health_queue_payload
from agentsassemble.room_friend_dms import (
    append_live_agent_dm_reply,
    enqueue_room_friend_direct_dm,
    read_live_agent_dm_events,
    room_friend_dm_payload,
)
from agentsassemble.room_friends import delete_room_friend, room_friends_payload, upsert_room_friend
from agentsassemble.identity_store import default_identity_db_path
from agentsassemble.room_members import is_room_member_muted, mark_thinking, room_members_payload
from agentsassemble.room_websocket import (
    CLOSE_PROTOCOL_ERROR,
    MessageAssembler,
    WebSocketProtocolError,
    compute_accept_key,
    encode_close,
    is_websocket_upgrade,
)
from agentsassemble.ws_room_session import (
    WS_TICKET_TTL_SECONDS,
    WsRoomDeps,
    WsRoomSession,
    WsTicketStore,
)
from agentsassemble.room_users import configure_room_users_store
from agentsassemble.room_settings import room_settings_payload, update_room_settings
from agentsassemble.user_profile import read_user_profile, update_user_profile
from agentsassemble.room_invite import (
    active_sessions_summary,
    configure_room_invite_store,
    default_room_invite_store_path,
    generate_runtime_host_token,
    get_host_token,
    get_public_url,
    has_runtime_host_token,
    host_gate_required,
    normalize_public_room_url,
    set_runtime_host_token,
    set_runtime_public_url,
    verify_host_token,
    verify_session_token,
)
from agentsassemble.meeting_events import (
    FLOW_METADATA_KEYS,
    ROOM_TOPIC_LIMIT,
    append_live_event,
    append_lobby_event_to_file,
    append_side_chat_event_to_file,
    clean_lobby_text,
    iter_lobby_events_newest_first,
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
from agentsassemble.sse_cadence import SSE_EVENT_POLL_INTERVAL_SECONDS, SSE_KEEPALIVE_INTERVAL_SECONDS

TAB_LABELS = {"lobby": "로비", "live": "실황", "board": "작전판", "archive": "아카이브"}
TABS = ["lobby", "live", "board", "archive"]
LIVE_AGENT_ROOM_LOBBY_EVENT_LIMIT = PROBE_REPLY_EVENT_TAIL_LIMIT
SSE_ERROR_MESSAGE_LIMIT = 500
REMOTE_LOBBY_REQUESTER = None
MAX_READINESS_PROBE_AGENTS = 10
OFFICIAL_ROUND_SMOKE_ERROR = "official round smoke could not be run"
SESSION_SMOKE_ERROR = "session smoke could not be run"
REAL_SESSION_SMOKE_APPROVAL_REQUIRED_MESSAGE = (
    "Real session smoke requires current operator approval before starting real providers."
)
REAL_SESSION_SMOKE_CONFIG_REQUIRED_MESSAGE = (
    "Real session smoke requires explicit live-agent, council, and agent config paths."
)
REAL_SESSION_SMOKE_PROBE_REDACTION = "[redacted real session smoke probe]"


def _safe_live_agent_flow_agents(
    output_root: Path,
    *,
    meeting_id: str = "",
    quota_viewer: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    payload = _live_agent_roster_with_admission_evidence(
        output_root,
        {
            "agents": filter_live_agent_roster(
                read_live_agents(output_root),
                meeting_id=meeting_id,
            )
        },
    )
    safe_payload = safe_live_agent_roster_payload(payload, quota_viewer=quota_viewer)
    agents = safe_payload.get("agents")
    return agents if isinstance(agents, list) else []


class LiveAgentFlowSupervisor:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root
        self._lock = threading.RLock()
        self._runs: dict[str, dict[str, object]] = {}

    def start(self, payload: dict[str, object]) -> dict[str, object]:
        meeting_id = clean_lobby_text(payload.get("meeting_id"), limit=128)
        if not meeting_id:
            raise ValueError("Meeting id is required.")
        meeting_dir = _safe_meeting_dir(self.output_root, meeting_id)
        if not meeting_dir.exists():
            raise _meeting_not_found_error(meeting_id)
        meeting = _read_meeting_record(meeting_dir)
        topic = (
            clean_lobby_text(payload.get("topic"), limit=ROOM_TOPIC_LIMIT)
            or clean_lobby_text(
                meeting.get("display_topic") or meeting.get("topic") or meeting.get("question"),
                limit=ROOM_TOPIC_LIMIT,
            )
            or "자유토론"
        )
        options = FlowOptions.from_payload(payload)
        with self._lock:
            existing = self._runs.get(meeting_id)
            if existing and self._public_state(existing).get("status") == "running":
                return self.status(meeting_id=meeting_id)
            flow_id = f"flow-{uuid4().hex[:8]}"
            now = datetime.now(UTC)
            # duration_seconds <= 0 means an unlimited discussion: no deadline is
            # set, so _flow_time_expired never trips and the flow runs until it is
            # stopped manually (or hits a turn budget).
            deadline = None if options.duration_seconds <= 0 else now + timedelta(seconds=options.duration_seconds)
            previous_modes, agent_count = self._set_bound_agents_to_flow(meeting, meeting_id)
            state: dict[str, object] = {
                "flow_id": flow_id,
                "meeting_id": meeting_id,
                "topic": topic,
                "status": "running",
                "started_at": now.isoformat(),
                "deadline_at": deadline.isoformat() if deadline is not None else "",
                "duration_seconds": options.duration_seconds,
                "tick_interval": options.tick_interval,
                "cooldown": options.cooldown,
                "max_agent_turns": options.max_agent_turns,
                "max_total_turns": options.max_total_turns,
                "max_silence_seconds": options.max_silence_seconds,
                "policy": options.flow_policy,
                "agent_count": agent_count,
                "total_turns": 0,
                "last_activity_at": now.isoformat(),
            }
            append_lobby_event(
                self.output_root,
                {
                    "name": "Play Mode",
                    "side": "other",
                    "kind": "message",
                    "message": f"시간제 자유토론 시작: {topic}",
                    "actor_id": "flow",
                    **self._flow_event_metadata(state, event_type="started"),
                },
                allow_flow_metadata=True,
            )
            stop_event = threading.Event()
            run = {
                "state": state,
                "options": options,
                "previous_modes": previous_modes,
                "stop_event": stop_event,
                "last_silence_check_at": now,
            }
            thread = threading.Thread(
                target=self._run_flow,
                args=(meeting_id,),
                daemon=True,
                name=f"AgentsAssembleFlow-{meeting_id}",
            )
            run["thread"] = thread
            self._runs[meeting_id] = run
            thread.start()
            return self.status(meeting_id=meeting_id)

    def status(
        self,
        *,
        meeting_id: str = "",
        quota_viewer: dict[str, object] | None = None,
    ) -> dict[str, object]:
        clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
        events = read_lobby(self.output_root, meeting_id=clean_meeting_id)
        with self._lock:
            run = self._selected_run(clean_meeting_id)
            if run is None:
                flow = _restored_flow_state(events, meeting_id=clean_meeting_id)
                flow_meeting_id = clean_lobby_text((flow or {}).get("meeting_id"), limit=128) or clean_meeting_id
                if not clean_meeting_id and flow_meeting_id:
                    events = read_lobby(self.output_root, meeting_id=flow_meeting_id)
                return {
                    "flow": flow or {"status": "idle"},
                    "agents": _safe_live_agent_flow_agents(
                        self.output_root,
                        meeting_id=flow_meeting_id,
                        quota_viewer=quota_viewer,
                    ),
                    "events": events,
                    "flow_events": _flow_events_for_state(events, flow),
                }
            self._refresh_counts_locked(run)
            flow = self._public_state(run)
            flow_meeting_id = clean_lobby_text(flow.get("meeting_id"), limit=128)
            if not clean_meeting_id and flow_meeting_id:
                events = read_lobby(self.output_root, meeting_id=flow_meeting_id)
            return {
                "flow": flow,
                "agents": _safe_live_agent_flow_agents(
                    self.output_root,
                    meeting_id=flow_meeting_id or clean_meeting_id,
                    quota_viewer=quota_viewer,
                ),
                "events": events,
                "flow_events": _flow_events_for_state(events, flow),
            }

    def stop(self, payload: dict[str, object]) -> dict[str, object]:
        meeting_id = clean_lobby_text(payload.get("meeting_id"), limit=128)
        with self._lock:
            run = self._selected_run(meeting_id)
            if run is None:
                return {"flow": {"status": "idle"}}
            stop_event = run.get("stop_event")
            if isinstance(stop_event, threading.Event):
                stop_event.set()
        thread = run.get("thread")
        if isinstance(thread, threading.Thread):
            thread.join(timeout=2)
        with self._lock:
            if self._public_state(run).get("status") == "running":
                self._finish_locked(run, "stopped")
            events = read_lobby(self.output_root)
            flow = self._public_state(run)
            return {
                "flow": flow,
                "agents": _safe_live_agent_flow_agents(self.output_root),
                "events": events,
                "flow_events": _flow_events_for_state(events, flow),
            }

    def _run_flow(self, meeting_id: str) -> None:
        try:
            while True:
                with self._lock:
                    run = self._runs.get(meeting_id)
                    if run is None:
                        return
                    state = run["state"] if isinstance(run.get("state"), dict) else {}
                    options = run["options"] if isinstance(run.get("options"), FlowOptions) else FlowOptions()
                    stop_event = run.get("stop_event")
                    if not isinstance(stop_event, threading.Event):
                        return
                    if stop_event.is_set():
                        self._finish_locked(run, "stopped")
                        return
                    self._refresh_counts_locked(run)
                    if self._flow_time_expired(state) or self._flow_turn_budget_exhausted(state):
                        self._finish_locked(run, "finished")
                        return
                    self._mark_silence_check_locked(run)
                if stop_event.wait(max(0.01, options.tick_interval)):
                    continue
        except Exception:
            # A tick must never leave the flow stuck "running" with agents pinned
            # to flow engagement mode; finish and restore modes on any failure.
            with self._lock:
                run = self._runs.get(meeting_id)
                if run is None:
                    return
                try:
                    self._finish_locked(run, "stopped")
                except Exception:
                    state = run.get("state")
                    if isinstance(state, dict):
                        state["status"] = "stopped"
                    self._restore_previous_modes(run)

    def _selected_run(self, meeting_id: str) -> dict[str, object] | None:
        clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
        if clean_meeting_id:
            return self._runs.get(clean_meeting_id)
        if not self._runs:
            return None
        return next(reversed(self._runs.values()))

    def _set_bound_agents_to_flow(self, meeting: dict[str, object], meeting_id: str) -> tuple[dict[str, str], int]:
        previous_modes: dict[str, str] = {}
        for agent in read_live_agents(self.output_root):
            agent_id = clean_lobby_text(agent.get("agent_id"), limit=64)
            if not agent_id:
                continue
            if clean_lobby_text(agent.get("meeting_id"), limit=128) != meeting_id:
                continue
            admission = _live_agent_admission_details_from_meeting(meeting, agent, agent_id=agent_id)
            if admission.get("host_approved_binding") is not True:
                continue
            previous_modes[agent_id] = clean_lobby_text(agent.get("engagement_mode"), limit=64) or "mentioned"
            update_live_agent_engagement(self.output_root, agent_id, "flow")
        return previous_modes, len(previous_modes)

    def _restore_previous_modes(self, run: dict[str, object]) -> None:
        previous_modes = run.get("previous_modes")
        if not isinstance(previous_modes, dict):
            return
        for agent_id, mode in previous_modes.items():
            try:
                update_live_agent_engagement(self.output_root, str(agent_id), str(mode))
            except ValueError:
                continue

    def _refresh_counts_locked(self, run: dict[str, object]) -> None:
        state = run["state"] if isinstance(run.get("state"), dict) else {}
        flow_id = clean_lobby_text(state.get("flow_id"), limit=128)
        if not flow_id:
            return
        try:
            events = read_lobby(self.output_root, limit=None)
        except OSError:
            return
        total_turns = flow_turn_count(events, flow_id=flow_id)
        state["total_turns"] = total_turns
        last_activity = _latest_flow_activity_at(events, flow_id=flow_id) or clean_lobby_text(state.get("started_at"), limit=64)
        if last_activity:
            state["last_activity_at"] = last_activity

    def _flow_time_expired(self, state: dict[str, object]) -> bool:
        deadline = _parse_iso_datetime(state.get("deadline_at"))
        return deadline is not None and datetime.now(UTC) >= deadline

    def _flow_turn_budget_exhausted(self, state: dict[str, object]) -> bool:
        max_total = int(state.get("max_total_turns") or 0)
        total = int(state.get("total_turns") or 0)
        return bool(max_total and total >= max_total)

    def _mark_silence_check_locked(self, run: dict[str, object]) -> None:
        state = run["state"] if isinstance(run.get("state"), dict) else {}
        max_silence = float(state.get("max_silence_seconds") or 0)
        if max_silence <= 0:
            return
        last_activity = _parse_iso_datetime(state.get("last_activity_at"))
        if last_activity is None:
            return
        now = datetime.now(UTC)
        if (now - last_activity).total_seconds() < max_silence:
            return
        last_silence_check_at = run.get("last_silence_check_at")
        if isinstance(last_silence_check_at, datetime) and (now - last_silence_check_at).total_seconds() < max_silence:
            return
        run["last_silence_check_at"] = now

    def _finish_locked(self, run: dict[str, object], status: str) -> None:
        state = run["state"] if isinstance(run.get("state"), dict) else {}
        if state.get("status") != "running":
            return
        self._refresh_counts_locked(run)
        state["status"] = status
        state["finished_at"] = datetime.now(UTC).isoformat()
        append_lobby_event(
            self.output_root,
            {
                "name": "Play Mode",
                "side": "other",
                "kind": "message",
                "message": "시간제 자유토론 종료" if status == "finished" else "시간제 자유토론 중지",
                "actor_id": "flow",
                **self._flow_event_metadata(state, event_type=status),
            },
            allow_flow_metadata=True,
        )
        self._restore_previous_modes(run)

    def _flow_event_metadata(self, state: dict[str, object], *, event_type: str) -> dict[str, object]:
        return {
            "flow_id": state.get("flow_id") or "",
            "flow_meeting_id": state.get("meeting_id") or "",
            "flow_event_type": event_type,
            "flow_status": state.get("status") or "",
            "flow_topic": state.get("topic") or "",
            "flow_policy": state.get("policy") or "",
            "flow_duration_seconds": int(float(state.get("duration_seconds") or 0)),
            "flow_tick_interval": int(float(state.get("tick_interval") or 0)),
            "flow_cooldown": int(float(state.get("cooldown") or 0)),
            "flow_max_agent_turns": int(state.get("max_agent_turns") or 0),
            "flow_max_total_turns": int(state.get("max_total_turns") or 0),
            "flow_max_silence_seconds": int(float(state.get("max_silence_seconds") or 0)),
            "flow_total_turns": int(state.get("total_turns") or 0),
            "flow_agent_count": int(state.get("agent_count") or 0),
            "flow_started_at": state.get("started_at") or "",
            "flow_deadline_at": state.get("deadline_at") or "",
        }

    def _public_state(self, run: dict[str, object]) -> dict[str, object]:
        state = dict(run["state"] if isinstance(run.get("state"), dict) else {})
        deadline = _parse_iso_datetime(state.get("deadline_at"))
        if deadline is not None and state.get("status") == "running":
            state["remaining_seconds"] = max(0.0, (deadline - datetime.now(UTC)).total_seconds())
        return state


def _latest_flow_activity_at(events: list[dict[str, object]], *, flow_id: str) -> str:
    for event in reversed(events):
        if str(event.get("flow_id") or "") != flow_id:
            continue
        if str(event.get("flow_action") or "") or str(event.get("flow_event_type") or "") in {"started", "nudge"}:
            return clean_lobby_text(event.get("created_at"), limit=64)
    return ""


def _restored_flow_state(events: list[dict[str, object]], *, meeting_id: str = "") -> dict[str, object] | None:
    context = _latest_flow_context(events, meeting_id=meeting_id)
    if context is None:
        return None
    flow_id = clean_lobby_text(context.get("flow_id"), limit=128)
    if not flow_id:
        return None
    event_type = clean_lobby_text(context.get("flow_event_type"), limit=64)
    status = clean_lobby_text(context.get("flow_status"), limit=64)
    if event_type == "started":
        status = "running"
    elif event_type in FLOW_TERMINAL_EVENT_TYPES:
        status = event_type
    status = status or "running"
    deadline = _parse_iso_datetime(context.get("flow_deadline_at"))
    if status == "running" and deadline is not None and datetime.now(UTC) >= deadline:
        status = "finished"
    state: dict[str, object] = {
        "flow_id": flow_id,
        "meeting_id": clean_lobby_text(context.get("flow_meeting_id"), limit=128),
        "topic": clean_lobby_text(context.get("flow_topic"), limit=ROOM_TOPIC_LIMIT),
        "policy": clean_lobby_text(context.get("flow_policy"), limit=64) or "turn_based_floor",
        "status": status,
        "started_at": clean_lobby_text(context.get("flow_started_at"), limit=64),
        "deadline_at": clean_lobby_text(context.get("flow_deadline_at"), limit=64),
        "duration_seconds": _payload_nonnegative_float(context.get("flow_duration_seconds"), 0.0),
        "tick_interval": _payload_nonnegative_float(context.get("flow_tick_interval"), 0.0),
        "cooldown": _payload_nonnegative_float(context.get("flow_cooldown"), 0.0),
        "max_agent_turns": _payload_nonnegative_int(context.get("flow_max_agent_turns"), 0),
        "max_total_turns": _payload_nonnegative_int(context.get("flow_max_total_turns"), 0),
        "max_silence_seconds": _payload_nonnegative_float(context.get("flow_max_silence_seconds"), 0.0),
        "agent_count": _payload_nonnegative_int(context.get("flow_agent_count"), 0),
        "total_turns": flow_turn_count(events, flow_id=flow_id),
        "last_activity_at": _latest_flow_activity_at(events, flow_id=flow_id)
        or clean_lobby_text(context.get("created_at"), limit=64),
    }
    if status == "running" and deadline is not None:
        state["remaining_seconds"] = max(0.0, (deadline - datetime.now(UTC)).total_seconds())
    if event_type in FLOW_TERMINAL_EVENT_TYPES:
        state["finished_at"] = clean_lobby_text(context.get("created_at"), limit=64)
    return state


def _latest_flow_context(events: list[dict[str, object]], *, meeting_id: str = "") -> dict[str, object] | None:
    scoped_meeting_id = clean_lobby_text(meeting_id, limit=128)
    latest: dict[str, object] | None = None
    latest_flow_id = ""
    for event in events:
        flow_id = clean_lobby_text(event.get("flow_id"), limit=128)
        if not flow_id:
            continue
        event_meeting_id = clean_lobby_text(event.get("flow_meeting_id"), limit=128)
        if scoped_meeting_id and event_meeting_id != scoped_meeting_id:
            continue
        event_type = clean_lobby_text(event.get("flow_event_type"), limit=64)
        if event_type == "started":
            latest = event
            latest_flow_id = flow_id
            continue
        if event_type in FLOW_TERMINAL_EVENT_TYPES and latest is not None and flow_id == latest_flow_id:
            latest = event
    return latest


def _flow_events_for_state(events: list[dict[str, object]], flow: dict[str, object] | None) -> list[dict[str, object]]:
    if not isinstance(flow, dict):
        return []
    flow_id = clean_lobby_text(flow.get("flow_id"), limit=128)
    if not flow_id:
        return []
    return [event for event in events if clean_lobby_text(event.get("flow_id"), limit=128) == flow_id]


def _parse_iso_datetime(value: object) -> datetime | None:
    text = clean_lobby_text(value, limit=64)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
REAL_SESSION_SMOKE_REPLY_REDACTION = "[redacted real session smoke reply]"
LIVE_AGENT_TURN_LOCK = threading.Lock()
LIVE_AGENT_LOBBY_LOCK = threading.RLock()
REAL_SESSION_SMOKE_REDACTED_SOURCE_EVENT_IDS: set[str] = set()
MAX_LIVE_AGENT_SEQUENCE_TURNS = 12
MAX_LIVE_AGENT_ROUND_BATCH = 8
LIVE_AGENT_ROUND_SCHEDULER_LOCKS: dict[str, threading.RLock] = {}
LIVE_AGENT_ROUND_SCHEDULER_LOCKS_LOCK = threading.Lock()
DEFAULT_SESSION_RUN_MONITOR_INTERVAL_SECONDS = 30.0
MIN_SESSION_RUN_MONITOR_INTERVAL_SECONDS = 1.0
SESSION_RUN_MONITOR_ERROR = "Live-agent session run monitor failed."
SESSION_ENSURE_REASON_RESIDENT_SESSION_ID_DRIFT = "resident_session_id_drift"
SESSION_ENSURE_REASON_STALE_LOBBY_OBSERVATION = "stale_lobby_observation"
SESSION_ENSURE_REASON_STALE_LIVE_OBSERVATION = "stale_live_observation"
SESSION_ENSURE_REASONS = {
    SESSION_ENSURE_REASON_RESIDENT_SESSION_ID_DRIFT,
    SESSION_ENSURE_REASON_STALE_LOBBY_OBSERVATION,
    SESSION_ENSURE_REASON_STALE_LIVE_OBSERVATION,
}
HEALTH_WATCHDOG_REASON_EVENT_TYPES = {"stale_watchdog", "stale_watchdog_stop_failed"}
HEALTH_RESTART_FAILED_REASON_EVENT_TYPE = "restart_failed"
HEALTH_RECOVERED_UNKNOWN_REASON_EVENT_TYPE = "recovered_unknown"
HEALTH_RECOVERED_UNKNOWN_REASON = "orphan running record marked unknown"
LIVE_AGENT_ADMISSION_HEALTH_STATUSES = (
    "bound_to_meeting",
    "binding_conflict",
    "meeting_lobby_only",
    "meeting_missing",
    "lobby_only",
    "unknown",
)
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
        meeting = infer_live_status(
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


def build_meeting_payload(
    meeting_dir: Path,
    now: float | None = None,
    *,
    output_root: Path | None = None,
) -> dict[str, object]:
    meeting, _, has_final_record = _load_meeting_record(meeting_dir)
    meeting = infer_live_status(
        meeting,
        meeting_dir,
        has_final_record=has_final_record,
        now=now,
    )
    lifecycle_live_agents = _lifecycle_live_agents_for_meeting(
        output_root or _output_root_for_meeting_dir(meeting_dir),
        meeting,
    )
    artifacts = {
        name: _read_optional(meeting_dir / name)
        for name in ("agenda.md", "transcript.md", "decision.md", "room-log.md", "meeting.json")
    }
    if not (meeting_dir / "transcript.md").exists() and not has_final_record:
        artifacts["transcript.md"] = projected_live_transcript_text(meeting_dir, meeting=meeting)
    artifacts.update(_shared_memory_artifacts(meeting_dir, meeting=meeting, has_final_record=has_final_record))
    tasks = {
        task_path.name: task_path.read_text(encoding="utf-8")
        for task_path in sorted((meeting_dir / "tasks").glob("*.md"))
    }
    return_packets = {
        packet_path.name: packet_path.read_text(encoding="utf-8")
        for packet_path in sorted((meeting_dir / "return_packets").glob("*.md"))
    }
    review_checkpoints = {
        checkpoint_path.name: checkpoint_path.read_text(encoding="utf-8")
        for checkpoint_path in sorted((meeting_dir / "review_checkpoints").glob("*.*"))
        if checkpoint_path.suffix in {".md", ".json"}
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
        "lifecycle": project_meeting_lifecycle(meeting_dir, now=now, live_agents=lifecycle_live_agents),
        "artifacts": artifacts,
        "tasks": tasks,
        "return_packets": return_packets,
        "review_checkpoints": review_checkpoints,
        "research": research,
        "research_json": research_json,
        "live_events": read_live_events(meeting_dir),
    }


WORKROOM_QUEUE_ARTIFACT_PATHS = (
    "transcript.md",
    "decision.md",
    "shared_memory/rolling-summary.md",
    "shared_memory/action-items.md",
    "shared_memory/open-questions.md",
)
WORKROOM_QUEUE_SCOPE_OVERLAP_LIMIT = 5
WORKROOM_QUEUE_SCOPE_SUMMARIES = {"scope_overlap_evidence", "no_obvious_overlaps"}
WORKROOM_QUEUE_SCOPE_KINDS = {"file", "dir"}
WORKROOM_QUEUE_SCOPE_UNSAFE_SEGMENT_MARKERS = (
    "authorization",
    "auth_ref",
    "api-key",
    "api_key",
    "apikey",
    "x-api-key",
    "bearer",
    "credential",
    "password",
    "secret",
    "session",
    "token",
    "cookie",
)

SAFE_MEETING_STREAM_EVENT_STRING_FIELDS = (
    "id",
    "event_id",
    "created_at",
    "kind",
    "meeting_id",
    "channel",
    "audience",
    "actor_id",
    "target_agent_id",
    "source_event_id",
    "role_id",
    "display_name",
    "artifact_kind",
    "round",
    "turn_id",
    "engagement_mode",
    "confidence",
    "retry_status",
)
SAFE_MEETING_STREAM_TEXT_FIELDS = (
    "content",
    "message",
    "summary",
    "position",
    "change_reason",
    "remaining_resistance",
)
PRIVATE_MEETING_STREAM_CHANNELS = {"review"}
PRIVATE_MEETING_STREAM_KINDS = {"live_agent_turn_request"}


def build_workroom_queue_payload(
    meeting_dir: Path,
    now: float | None = None,
    *,
    output_root: Path | None = None,
) -> dict[str, object]:
    meeting, _, has_final_record = _load_meeting_record(meeting_dir)
    meeting = infer_live_status(
        meeting,
        meeting_dir,
        has_final_record=has_final_record,
        now=now,
    )
    lifecycle_live_agents = _lifecycle_live_agents_for_meeting(
        output_root or _output_root_for_meeting_dir(meeting_dir),
        meeting,
    )
    return {
        "meeting_id": clean_lobby_text(meeting.get("meeting_id") or meeting_dir.name, limit=128),
        "lifecycle": project_meeting_lifecycle(
            meeting_dir,
            now=now,
            live_agents=lifecycle_live_agents,
        ),
        "artifacts": {
            path: {"available": _workroom_artifact_available(meeting_dir, path)}
            for path in WORKROOM_QUEUE_ARTIFACT_PATHS
        },
        "return_packets": {
            "count": _count_existing_files(meeting_dir / "return_packets", {".md"}),
        },
        "review_checkpoints": {
            "count": _count_existing_stems(meeting_dir / "review_checkpoints", {".md", ".json"}),
        },
        "task_scope": _workroom_task_scope_payload(meeting_dir, meeting),
    }


def _workroom_artifact_available(meeting_dir: Path, artifact_path: str) -> bool:
    path = meeting_dir / artifact_path
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _count_existing_files(root: Path, suffixes: set[str]) -> int:
    if not root.exists():
        return 0
    count = 0
    for path in root.iterdir():
        if path.is_file() and path.suffix in suffixes:
            count += 1
    return count


def _count_existing_stems(root: Path, suffixes: set[str]) -> int:
    if not root.exists():
        return 0
    stems = {
        path.stem
        for path in root.iterdir()
        if path.is_file() and path.suffix in suffixes
    }
    return len(stems)


def _workroom_task_scope_payload(meeting_dir: Path, meeting: dict[str, object]) -> dict[str, object]:
    report = _read_workroom_task_scope_report(meeting_dir)
    summary_source = report if report is not None else meeting.get("task_scope_report")
    if not isinstance(summary_source, dict):
        summary_source = {}
    overlaps = _safe_workroom_task_scope_overlaps(
        report.get("overlaps") if isinstance(report, dict) else []
    )
    overlap_count = max(
        _safe_nonnegative_int(summary_source.get("overlap_count")),
        len(overlaps),
    )
    return {
        "available": bool(report or summary_source),
        "summary": _safe_workroom_task_scope_summary(summary_source.get("summary")),
        "overlap_count": overlap_count,
        "candidate_count_total": _safe_nonnegative_int(summary_source.get("candidate_count_total")),
        "overlaps": overlaps,
        "overlaps_truncated": bool(
            summary_source.get("overlaps_truncated")
            or (report and len(report.get("overlaps") if isinstance(report.get("overlaps"), list) else []) > len(overlaps))
        ),
    }


def _read_workroom_task_scope_report(meeting_dir: Path) -> dict[str, object] | None:
    try:
        payload = json.loads((meeting_dir / "task_scope_report.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _safe_workroom_task_scope_summary(value: object) -> str:
    summary = str(value or "").strip()
    return summary if summary in WORKROOM_QUEUE_SCOPE_SUMMARIES else "unknown"


def _safe_workroom_task_scope_overlaps(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    overlaps: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        token = _safe_workroom_scope_token(item.get("token"))
        if kind not in WORKROOM_QUEUE_SCOPE_KINDS or not token:
            continue
        overlaps.append(
            {
                "kind": kind,
                "token": token,
            }
        )
        if len(overlaps) >= WORKROOM_QUEUE_SCOPE_OVERLAP_LIMIT:
            break
    return overlaps


def _safe_workroom_scope_token(value: object) -> str:
    token = str(value or "").strip().strip("`'\"")
    if not token or len(token) > 160:
        return ""
    if token.startswith(("/", "~")) or "://" in token or "\\" in token:
        return ""
    segments = [segment for segment in token.split("/") if segment]
    if len(segments) < 2 or any(segment in {".", ".."} for segment in segments):
        return ""
    if any(_workroom_scope_segment_looks_sensitive(segment) for segment in segments):
        return ""
    first = segments[0].rstrip(".")
    if "." in first or ":" in token:
        return ""
    if token.endswith("/"):
        return token if all(re.fullmatch(r"[A-Za-z0-9._-]+", segment) for segment in segments) else ""
    if not re.fullmatch(r"(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+\.[A-Za-z0-9]{1,8}", token):
        return ""
    return token


def _workroom_scope_segment_looks_sensitive(segment: str) -> bool:
    lowered = segment.casefold()
    if segment.startswith((".", "-")) or "=" in segment:
        return True
    return any(marker in lowered for marker in WORKROOM_QUEUE_SCOPE_UNSAFE_SEGMENT_MARKERS)


def _safe_nonnegative_int(value: object) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    if not math.isfinite(number):
        return 0
    try:
        return max(0, int(number))
    except (TypeError, ValueError, OverflowError):
        return 0


def project_meeting_stream_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    projected: list[dict[str, object]] = []
    for event in events:
        safe_event = _project_meeting_stream_event(event)
        if safe_event is not None:
            projected.append(safe_event)
    return projected


def _project_meeting_stream_event(event: dict[str, object]) -> dict[str, object] | None:
    kind = clean_lobby_text(event.get("kind"), limit=64)
    channel = clean_lobby_text(event.get("channel"), limit=32)
    audience = clean_lobby_text(event.get("audience"), limit=64)
    if channel in PRIVATE_MEETING_STREAM_CHANNELS:
        return None
    if kind in PRIVATE_MEETING_STREAM_KINDS or audience.startswith("agent:"):
        return None
    safe: dict[str, object] = {}
    for field in SAFE_MEETING_STREAM_EVENT_STRING_FIELDS:
        value = clean_lobby_text(event.get(field), limit=256)
        if value:
            safe[field] = value
    if isinstance(event.get("official_record"), bool):
        safe["official_record"] = event["official_record"]
    for field in ("turn_index", "retry_attempts"):
        value = event.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            safe[field] = value
    for field in ("artifact_path", "artifact_json_path"):
        value = _safe_meeting_stream_relative_path(event.get(field))
        if value:
            safe[field] = value
    for field in SAFE_MEETING_STREAM_TEXT_FIELDS:
        value = clean_lobby_text(event.get(field), limit=2000)
        if value:
            safe[field] = value
    return safe if safe.get("id") else None


def _safe_meeting_stream_relative_path(value: object) -> str:
    text = clean_lobby_text(value, limit=256)
    if not text:
        return ""
    if text.startswith(("/", "\\", "~")) or "\\" in text or ":" in text:
        return ""
    parts = [part for part in text.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        return ""
    return "/".join(parts)


def build_meeting_stream_payload(
    meeting_dir: Path,
    now: float | None = None,
    *,
    output_root: Path | None = None,
) -> dict[str, object]:
    meeting, _, has_final_record = _load_meeting_record(meeting_dir)
    meeting = infer_live_status(
        meeting,
        meeting_dir,
        has_final_record=has_final_record,
        now=now,
    )
    meeting_id = clean_lobby_text(meeting.get("meeting_id") or meeting_dir.name, limit=128)
    lifecycle_live_agents = _lifecycle_live_agents_for_meeting(
        output_root or _output_root_for_meeting_dir(meeting_dir),
        meeting,
    )
    return {
        "meeting": {
            "meeting_id": meeting_id,
            "topic": clean_lobby_text(meeting.get("topic"), limit=ROOM_TOPIC_LIMIT),
            "question": clean_lobby_text(meeting.get("question"), limit=ROOM_TOPIC_LIMIT),
            "live_status": clean_lobby_text(meeting.get("live_status"), limit=64),
        },
        "lifecycle": project_meeting_lifecycle(
            meeting_dir,
            now=now,
            live_agents=lifecycle_live_agents,
        ),
        "live_events": project_meeting_stream_events(read_live_events(meeting_dir)),
    }


def _output_root_for_meeting_dir(meeting_dir: Path) -> Path | None:
    parent = meeting_dir.parent
    if parent.name != "meetings":
        return None
    return parent.parent


def _lifecycle_live_agents_for_meeting(
    output_root: Path | None,
    meeting: dict[str, object],
) -> list[dict[str, object]]:
    if output_root is None:
        return []
    meeting_id = clean_lobby_text(meeting.get("meeting_id"), limit=128)
    agents = []
    for agent in read_live_agents(output_root):
        agent_id = clean_lobby_text(agent.get("agent_id"), limit=64)
        agent_meeting_id = clean_lobby_text(agent.get("meeting_id"), limit=128)
        if not meeting_id or agent_meeting_id != meeting_id:
            continue
        agents.append(
            {
                **agent,
                **_live_agent_admission_details_from_meeting(meeting, agent, agent_id=agent_id),
            }
        )
    return agents


def _shared_memory_artifacts(
    meeting_dir: Path,
    *,
    meeting: dict[str, object],
    has_final_record: bool,
) -> dict[str, str]:
    shared_dir = meeting_dir / "shared_memory"
    artifact_paths = {
        "shared_memory/rolling-summary.md": shared_dir / "rolling-summary.md",
        "shared_memory/open-questions.md": shared_dir / "open-questions.md",
        "shared_memory/action-items.md": shared_dir / "action-items.md",
        "shared_memory/index.json": shared_dir / "index.json",
    }
    existing = {
        key: path.read_text(encoding="utf-8")
        for key, path in artifact_paths.items()
        if path.exists()
    }
    if existing or has_final_record:
        return existing
    return projected_live_meeting_memory_artifacts(meeting_dir, meeting=meeting)


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


def serve_gui(
    host: str = "127.0.0.1",
    port: int = 8765,
    output_root: Path | None = None,
    *,
    public_url: str = "",
    host_token: str = "",
    start_public_tunnel: bool = False,
    live_agent_config: Path | None = None,
    live_agent_group_id: str = "",
    live_agent_auto_restart: bool = False,
    live_agent_max_restarts: int = 0,
    live_agent_restart_backoff_seconds: float = 5.0,
    live_agent_stale_restart_after_seconds: float = 0.0,
    frontend_dist_root: Path | None = None,
) -> None:
    root = output_root or Path(".agentsassemble")
    process_supervisor = LiveAgentProcessSupervisor(root)
    session_run_controller = LiveAgentSessionRunController(root)
    flow_supervisor = LiveAgentFlowSupervisor(root)
    public_tunnel_manager = PublicTunnelManager()
    session_run_monitor = LiveAgentSessionRunMonitor(
        root,
        process_supervisor,
        session_run_controller,
        default_server="",
    )
    handler = _make_handler(
        root,
        process_supervisor=process_supervisor,
        session_run_controller=session_run_controller,
        session_run_monitor=session_run_monitor,
        flow_supervisor=flow_supervisor,
        frontend_dist_root=frontend_dist_root,
        public_tunnel_manager=public_tunnel_manager,
    )
    server = ThreadingHTTPServer((host, port), handler)
    if not _is_loopback_host(host):
        print(
            f"WARNING: AgentsAssemble GUI bound to non-loopback host {host!r}; the control "
            "plane is unauthenticated and can launch local processes. Expose it only on trusted networks."
        )
    try:
        if host_token:
            set_runtime_host_token(host_token)
        if public_url:
            set_runtime_public_url(public_url)
        if (public_url or start_public_tunnel) and not get_host_token():
            generated_token = generate_runtime_host_token()
            print(f"AgentsAssemble host token: {generated_token}")
        process_supervisor.start_monitor()
        server_url = _local_server_url(server.server_address)
        public_tunnel_manager.set_local_url(server_url)
        session_run_monitor.default_server = server_url
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
        session_run_monitor.start()
        if start_public_tunnel:
            public_tunnel_manager.start()
        _print_gui_startup_banner(server_url, frontend_dist_root=frontend_dist_root)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AgentsAssemble GUI")
    finally:
        session_run_monitor.stop()
        public_tunnel_manager.stop()
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
    request_overrides: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    def ensure_from_run(run: dict[str, object]) -> dict[str, object]:
        request = _session_run_reconcile_request(run)
        if isinstance(request_overrides, dict):
            request.update(request_overrides)
        _assert_session_run_launch_approved(process_supervisor, request, default_server)
        return live_agent_session_ensure_payload(
            output_root,
            process_supervisor,
            request,
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


def _session_run_reconcile_request(run: dict[str, object]) -> dict[str, object]:
    request = dict(run.get("request") if isinstance(run.get("request"), dict) else {})
    meeting_id = str(run.get("meeting_id") or "").strip()
    group_id = str(run.get("group_id") or "").strip()
    if meeting_id:
        request["meeting_id"] = meeting_id
    if group_id:
        request["group_id"] = group_id
    return request


def _session_run_reconcile_launch_policy_targets(
    process_supervisor: LiveAgentProcessSupervisor,
    request: dict[str, object],
    default_server: str,
) -> list[tuple[object, str]]:
    targets: list[tuple[object, str]] = []
    seen: set[str] = set()

    def add_target(config_path: object, server: object) -> None:
        key = str(config_path or "").strip()
        if not key or key in seen:
            return
        seen.add(key)
        targets.append((config_path, str(server or default_server)))

    request_server = str(request.get("server") or default_server)
    add_target(request.get("live_agent_config_path"), request_server)

    group_id = str(request.get("group_id") or "").strip()
    snapshot_groups = getattr(process_supervisor, "snapshot_groups", None)
    if not group_id or not callable(snapshot_groups):
        return targets
    try:
        groups = snapshot_groups()
    except Exception:
        if not targets:
            raise ValueError(APPROVAL_REQUIRED_MESSAGE)
        return targets
    if not isinstance(groups, list):
        if not targets:
            raise ValueError(APPROVAL_REQUIRED_MESSAGE)
        return targets
    for group in groups:
        if not isinstance(group, dict):
            continue
        if str(group.get("group_id") or "").strip() != group_id:
            continue
        add_target(group.get("config_path"), group.get("server") or request_server)
    return targets


def _assert_session_run_launch_approved(
    process_supervisor: LiveAgentProcessSupervisor,
    request: dict[str, object],
    default_server: str,
) -> None:
    approved = _payload_bool(request.get("approve_real_providers"))
    for config_path, server in _session_run_reconcile_launch_policy_targets(process_supervisor, request, default_server):
        assert_resident_launch_approved(
            config_path,
            request=request,
            server=server,
            approved=approved,
        )


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
    if _operation_result_status(readiness.get("status")) != "ready":
        return True
    return _ready_session_requires_restart_for_stale_observation_lag(
        output_root,
        process_supervisor,
        {"meeting_id": meeting_id, "group_id": group_id},
        readiness,
    )


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
        self._last_tick_at = ""
        self._last_status = "not_started"
        self._last_result_count = 0
        self._last_error_type = ""

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
        results = _reconcile_live_agent_session_runs(
            self.output_root,
            self.process_supervisor,
            self.session_run_controller,
            default_server=self.default_server,
            summary="reconciled durable live-agent session runs during GUI runtime",
        )
        self._record_success(results)
        return results

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            thread = self._thread
            return {
                "running": bool(thread is not None and thread.is_alive()),
                "interval_seconds": self.interval_seconds,
                "last_tick_at": self._last_tick_at,
                "last_status": self._last_status,
                "last_result_count": self._last_result_count,
                "last_error_type": self._last_error_type,
            }

    def _loop(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                self.run_once()
            except Exception as error:
                self._record_failure(error)
            if stop_event.wait(self.interval_seconds):
                break

    def _record_failure(self, error: Exception) -> None:
        with self._lock:
            self._last_tick_at = datetime.now(UTC).isoformat()
            self._last_status = "failed"
            self._last_result_count = 0
            self._last_error_type = _safe_session_run_monitor_error_type(error)
        record_live_agent_operation(
            self.output_root,
            operation="session_run.monitor",
            status="failed",
            summary="live-agent session-run monitor failed",
            error=SESSION_RUN_MONITOR_ERROR,
            details={"error_type": _safe_session_run_monitor_error_type(error)},
        )

    def _record_success(self, results: list[dict[str, object]]) -> None:
        with self._lock:
            self._last_tick_at = datetime.now(UTC).isoformat()
            self._last_status = _session_run_monitor_result_status(results)
            self._last_result_count = len(results)
            self._last_error_type = ""


def _session_run_monitor_result_status(results: list[dict[str, object]]) -> str:
    if any(str(item.get("status") or "") == "failed" for item in results):
        return "failed"
    if any(str(item.get("status") or "") in {"running", "recovering", "starting", "degraded"} for item in results):
        return "degraded"
    return "ok"


def _safe_session_run_monitor_error_type(error: Exception) -> str:
    error_type = clean_lobby_text(type(error).__name__, limit=80)
    return error_type if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,79}", error_type) else "Exception"


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


def read_lobby(output_root: Path, limit: int | None = 80, *, meeting_id: str = "") -> list[dict[str, object]]:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    if not clean_meeting_id:
        # Global lobby: a plain global tail is what we want.
        return read_lobby_events(output_root / "lobby.jsonl", limit=limit)
    # Per-room: scan newest-first and keep this room's last `limit` events. The
    # lobby is one shared append-only log, so a global tail (read_lobby_events)
    # can contain zero of this room's events once other rooms' messages push
    # past the window — which made older rooms load empty. Filtering during the
    # backward scan (like the scroll-up pagination) fixes that.
    cap = limit if isinstance(limit, int) and limit > 0 else None
    collected: list[dict[str, object]] = []
    for event in iter_lobby_events_newest_first(output_root / "lobby.jsonl"):
        if clean_lobby_text(event.get("flow_meeting_id"), limit=128) != clean_meeting_id:
            continue
        collected.append(event)
        if cap is not None and len(collected) >= cap:
            break
    collected.reverse()  # oldest-last, matching read_lobby_events ordering
    return collected


LOBBY_HISTORY_PAGE_LIMIT = 50
LOBBY_HISTORY_MAX_PAGE_LIMIT = 200


def read_lobby_before(
    output_root: Path,
    *,
    before_event_id: str,
    limit: int = LOBBY_HISTORY_PAGE_LIMIT,
    meeting_id: str = "",
) -> dict[str, object]:
    """One page of history strictly older than before_event_id (newest-last).

    Streams the log backwards with the room filter applied during the scan,
    so a page is full even when other rooms' messages are interleaved.
    Returns {"events": [...], "has_more": bool} for scroll-up pagination.
    """
    clean_limit = max(1, min(int(limit or LOBBY_HISTORY_PAGE_LIMIT), LOBBY_HISTORY_MAX_PAGE_LIMIT))
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    anchor = clean_lobby_text(before_event_id, limit=128)
    events: list[dict[str, object]] = []
    has_more = False
    seen_anchor = not anchor
    for event in iter_lobby_events_newest_first(output_root / "lobby.jsonl"):
        if not seen_anchor:
            if str(event.get("id") or "") == anchor:
                seen_anchor = True
            continue
        if clean_meeting_id and clean_lobby_text(event.get("flow_meeting_id"), limit=128) != clean_meeting_id:
            continue
        if len(events) >= clean_limit:
            has_more = True
            break
        events.append(event)
    events.reverse()
    return {"events": events, "has_more": has_more}


def _filter_lobby_events_for_meeting(events: list[dict[str, object]], *, meeting_id: str = "") -> list[dict[str, object]]:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    if not clean_meeting_id:
        return events
    return [
        event
        for event in events
        if clean_lobby_text(event.get("flow_meeting_id"), limit=128) == clean_meeting_id
    ]


def append_lobby_event(
    output_root: Path,
    event: dict[str, object],
    *,
    live_agent_endpoint: bool = False,
    allow_flow_metadata: bool = False,
) -> dict[str, object]:
    with LIVE_AGENT_LOBBY_LOCK:
        return append_lobby_event_to_file(
            output_root / "lobby.jsonl",
            event,
            live_agent_endpoint=live_agent_endpoint,
            allow_flow_metadata=allow_flow_metadata,
        )


def lobby_payload_with_attachments(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    event = dict(payload)
    if "flow_meeting_id" in event:
        event["flow_meeting_id"] = clean_lobby_text(event.get("flow_meeting_id"), limit=128)
    if not clean_lobby_text(event.get("flow_meeting_id"), limit=128):
        implicit_meeting_id = _single_lobby_meeting_id(output_root)
        if implicit_meeting_id:
            event["flow_meeting_id"] = implicit_meeting_id
    if "attachments" in event:
        event["attachments"] = normalize_attachment_references(output_root, event.get("attachments"))
    return event


def _single_lobby_meeting_id(output_root: Path) -> str:
    meeting_ids = [
        clean_lobby_text(meeting.get("meeting_id"), limit=128)
        for meeting in list_meetings(output_root)
    ]
    meeting_ids = [meeting_id for meeting_id in meeting_ids if meeting_id]
    return meeting_ids[0] if len(meeting_ids) == 1 else ""


def _public_lobby_allows_room_scope(payload: dict[str, object]) -> bool:
    if not clean_lobby_text(payload.get("flow_meeting_id"), limit=128):
        return False
    control_keys = FLOW_METADATA_KEYS - {"flow_meeting_id"}
    return not any(clean_lobby_text(payload.get(key), limit=128) for key in control_keys)


def _side_chat_scope_id(value: object) -> str:
    return clean_lobby_text(value, limit=128)


def _side_chat_event_matches_meeting(event: dict[str, object], meeting_id: str) -> bool:
    if not meeting_id:
        return True
    return _side_chat_scope_id(event.get("flow_meeting_id")) == meeting_id


def _filter_side_chat_events_for_meeting(
    events: list[dict[str, object]],
    meeting_id: str | None,
) -> list[dict[str, object]]:
    scoped_meeting_id = _side_chat_scope_id(meeting_id)
    if not scoped_meeting_id:
        return events
    return [event for event in events if _side_chat_event_matches_meeting(event, scoped_meeting_id)]


def read_side_chat(
    output_root: Path,
    limit: int = 120,
    meeting_id: str | None = None,
) -> list[dict[str, object]]:
    return _filter_side_chat_events_for_meeting(
        read_side_chat_events(output_root / "side_chat.jsonl", limit=limit),
        meeting_id,
    )


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
        events = _filter_lobby_events_for_meeting(events, meeting_id=meeting_id or "")
        return {"stream": "lobby", "events": events}
    if stream == "side_chat":
        events = read_side_chat_events_after(output_root / "side_chat.jsonl", last_event_id)
        events = _filter_side_chat_events_for_meeting(events, meeting_id)
        return {"stream": "side_chat", "events": events}
    if stream == "roster":
        # Push-style member panel (R6): the SSE loop diffs the signature and
        # only emits a frame when the roster actually changes.
        members_payload = room_members_payload(
            output_root,
            read_live_agents(output_root),
            meeting_id=meeting_id or "",
            sessions=active_sessions_summary(),
        )
        members = members_payload.get("members") or []
        return {
            "stream": "roster",
            "meeting_id": meeting_id or "",
            "members": members,
            "payload_signature": json.dumps(members, ensure_ascii=False, sort_keys=True),
        }
    if stream == "meeting":
        if not meeting_id:
            raise ValueError("Meeting id is required for meeting event stream.")
        meeting_dir = _safe_meeting_dir(output_root, meeting_id)
        if not meeting_dir.exists():
            raise _meeting_not_found_error(meeting_id)
        try:
            events = project_meeting_stream_events(read_live_events_after(meeting_dir, last_event_id))
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
                meeting_payload = build_meeting_stream_payload(meeting_dir, output_root=output_root)
            except FileNotFoundError as error:
                raise _meeting_not_found_error(meeting_id) from error
            except json.JSONDecodeError:
                payload["meeting_stream_snapshot_pending"] = True
                payload["payload_signature"] = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            else:
                payload["meeting_stream_snapshot"] = meeting_payload
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


def model_catalog_payload() -> dict[str, object]:
    """API-provider lane catalog (master plan 1단계 B) for model selection (2단계).

    Distinct from provider_catalog_payload (the CLI/runtime provider registry):
    this is the static OpenAI-compatible model catalog (NVIDIA/OpenRouter/LM
    Studio/BYOK) the GUI offers when launching an `api_call` agent. Keys are never
    exposed — only a `key_present` boolean per provider."""
    from agentsassemble import provider_catalog

    return provider_catalog.catalog_payload()


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


def live_agents_payload(
    output_root: Path,
    *,
    meeting_id: str = "",
    agent_ids: list[str] | None = None,
    statuses: list[str] | None = None,
    safe: bool = False,
) -> dict[str, object]:
    payload = {
        "agents": filter_live_agent_roster(
            read_live_agents(output_root),
            meeting_id=meeting_id,
            agent_ids=agent_ids or [],
            statuses=statuses or [],
        )
    }
    if safe:
        return safe_live_agent_roster_payload(_live_agent_roster_with_admission_evidence(output_root, payload))
    return {
        "agents": [
            _live_agent_without_quota_fields(agent)
            for agent in payload["agents"]
            if isinstance(agent, dict)
        ]
    }


def _live_agent_without_quota_fields(agent: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in agent.items() if key not in LIVE_AGENT_QUOTA_FIELDS}


def _live_agent_roster_with_admission_evidence(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    agents = payload.get("agents") if isinstance(payload.get("agents"), list) else []
    return {
        "agents": [
            {
                **_live_agent_without_admission_evidence(agent),
                **_live_agent_roster_admission_details(output_root, agent),
                "admission_evidence_source": "meeting_record",
            }
            for agent in agents
            if isinstance(agent, dict)
        ]
    }


def _live_agent_without_admission_evidence(agent: dict[str, object]) -> dict[str, object]:
    admission_fields = {
        "admission_status",
        "host_approved_binding",
        "binding_role_id",
        "binding_provider_id",
        "binding_provider_kind",
        "binding_permission_profile_id",
        "binding_join_mode",
        "binding_conflicts",
        "admission_evidence_source",
    }
    return {key: value for key, value in agent.items() if key not in admission_fields}


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
    run_id: str = "",
    meeting_id: str = "",
    group_id: str = "",
    include_readiness: bool = False,
    output_root: Path | None = None,
    process_supervisor: LiveAgentProcessSupervisor | None = None,
) -> dict[str, object]:
    runs = session_run_controller.list_runs(limit=limit, run_id=run_id, meeting_id=meeting_id, group_id=group_id)
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
    readiness_by_target = _session_readiness_by_target(summary)
    return [
        {
            **run,
            "readiness": _session_run_readiness_overlay(run, readiness_by_target),
        }
        for run in runs
    ]


def _session_readiness_by_target(summary: dict[str, object]) -> dict[tuple[str, str], dict[str, object]]:
    items = summary.get("items") if isinstance(summary.get("items"), list) else []
    return {
        (str(item.get("meeting_id") or ""), str(item.get("group_id") or "")): item
        for item in items
        if isinstance(item, dict)
    }


def _session_run_readiness_overlay(
    run: dict[str, object],
    readiness_by_target: dict[tuple[str, str], dict[str, object]],
) -> dict[str, object]:
    meeting_id = _safe_session_run_health_identity(run.get("meeting_id"))
    group_id = _safe_session_run_health_identity(run.get("group_id"))
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
    return finalize_live_agent_meeting(
        meeting_dir,
        force=_payload_bool(payload.get("force")),
        close_pending=_payload_bool(payload.get("close_pending")),
    )


def live_agent_turn_preset_payload(output_root: Path, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    meeting_dir = _safe_meeting_dir(output_root, clean_meeting_id)
    if not clean_meeting_id or not meeting_dir.exists():
        raise ValueError(f"Meeting {clean_meeting_id or '(blank)'} was not found.")
    preset_turns = build_play_preset_turns(
        _read_meeting_record(meeting_dir),
        read_live_agents(output_root),
        meeting_id=clean_meeting_id,
        preset_id=str(payload.get("preset_id") or payload.get("preset") or ""),
        role_ids=_payload_role_ids(payload.get("role_ids")),
    )
    sequence = live_agent_turn_sequence_payload(
        output_root,
        clean_meeting_id,
        {
            "turns": preset_turns["turns"],
            "timeout_seconds": _payload_nonnegative_float(payload.get("timeout_seconds", payload.get("timeout")), 30.0),
            "stop_on_timeout": _payload_bool(payload.get("stop_on_timeout")),
        },
    )
    return {
        **sequence,
        "preset_id": preset_turns["preset_id"],
        "label": preset_turns["label"],
        "round_id": preset_turns["round_id"],
        "role_ids": preset_turns["role_ids"],
    }


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


def live_agent_session_resume_agent_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    live_agent_config_path = str(payload.get("live_agent_config_path") or payload.get("live_agent_config") or "").strip()
    session = resume_live_agent_session_agent(
        output_root,
        process_supervisor,
        server=str(payload.get("server") or default_server),
        live_agent_config_path=Path(live_agent_config_path) if live_agent_config_path else None,
        meeting_id=str(payload.get("meeting_id") or ""),
        group_id=str(payload.get("group_id") or ""),
        agent_id=str(payload.get("agent_id") or ""),
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
    stale_observation_restart_count = 0
    ensure_reason = ""
    if action == "none" and _ready_session_requires_restart_for_resident_session_drift(
        output_root,
        process_supervisor,
        payload,
        current,
        default_server=default_server,
    ):
        action = "restart"
        ensure_reason = SESSION_ENSURE_REASON_RESIDENT_SESSION_ID_DRIFT
    if action == "none":
        stale_observation_restart_count, ensure_reason = _stale_observation_restart_decision(
            output_root,
            process_supervisor,
            payload,
            current,
        )
        if stale_observation_restart_count > 0:
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
        session = live_agent_session_restart_payload(
            output_root,
            process_supervisor,
            payload,
            restart_count=stale_observation_restart_count if stale_observation_restart_count > 0 else None,
        )
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
    if ensure_reason:
        ensured["ensure_reason"] = ensure_reason
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


def _ready_session_requires_restart_for_stale_observation_lag(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    current: dict[str, object] | None,
) -> bool:
    return _stale_observation_restart_count(output_root, process_supervisor, payload, current) > 0


def _stale_observation_restart_count(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    current: dict[str, object] | None,
) -> int:
    restart_count, _reason = _stale_observation_restart_decision(output_root, process_supervisor, payload, current)
    return restart_count


def _stale_observation_restart_decision(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    current: dict[str, object] | None,
) -> tuple[int, str]:
    if not isinstance(current, dict) or _operation_result_status(current.get("status")) != "ready":
        return 0, ""
    meeting_id = str(current.get("meeting_id") or payload.get("meeting_id") or "").strip()
    group_id = str(current.get("group_id") or payload.get("group_id") or "").strip()
    if not meeting_id or not group_id:
        return 0, ""
    group = _find_session_process_group(_session_process_groups_snapshot(process_supervisor), group_id)
    if str(group.get("status") or "") != "running":
        return 0, ""
    stale_after_seconds = _observation_restart_stale_after_seconds(group)
    if stale_after_seconds <= 0:
        return 0, ""
    agent_ids = [
        _safe_session_run_health_identity(agent.get("agent_id"))
        for agent in _as_dict_list(group.get("agents"))
        if _safe_session_run_health_identity(agent.get("agent_id"))
    ]
    if not agent_ids:
        return 0, ""
    agents_by_id = {
        _safe_session_run_health_identity(agent.get("agent_id")): agent
        for agent in read_live_agents(output_root)
        if _safe_session_run_health_identity(agent.get("agent_id"))
    }
    restart_count = _payload_nonnegative_int(group.get("restart_count"), 0) + 1
    if _ready_session_has_stale_lobby_observation_lag(
        output_root,
        agent_ids,
        agents_by_id,
        stale_after_seconds=stale_after_seconds,
    ):
        return restart_count, SESSION_ENSURE_REASON_STALE_LOBBY_OBSERVATION
    if _ready_session_has_stale_live_observation_lag(
        output_root,
        meeting_id,
        agent_ids,
        agents_by_id,
        stale_after_seconds=stale_after_seconds,
    ):
        return restart_count, SESSION_ENSURE_REASON_STALE_LIVE_OBSERVATION
    return 0, ""


def _observation_restart_stale_after_seconds(group: dict[str, object]) -> float:
    if not _payload_bool(group.get("auto_restart")):
        return 0.0
    max_restarts = _payload_nonnegative_int(group.get("max_restarts"), 0)
    restart_count = _payload_nonnegative_int(group.get("restart_count"), 0)
    if max_restarts <= 0 or restart_count >= max_restarts:
        return 0.0
    return _payload_nonnegative_float(group.get("stale_restart_after_seconds"), 0.0)


def _ready_session_has_stale_lobby_observation_lag(
    output_root: Path,
    agent_ids: list[str],
    agents_by_id: dict[str, dict[str, object]],
    *,
    stale_after_seconds: float,
) -> bool:
    latest_lobby_event = _latest_lobby_event(output_root)
    latest_event_id = _safe_session_run_health_identity(latest_lobby_event.get("id"))
    latest_actor_id = _safe_session_run_health_identity(latest_lobby_event.get("actor_id"))
    if not latest_event_id or not _event_is_stale_for_observation_restart(latest_lobby_event, stale_after_seconds):
        return False
    for agent_id in agent_ids:
        agent = agents_by_id.get(agent_id, {})
        status = _live_agent_lobby_observation_status(
            latest_event_id,
            _safe_session_run_health_identity(agent.get("last_observed_event_id")),
            latest_actor_id=latest_actor_id,
            agent_id=agent_id,
        )
        if status == "behind":
            return True
    return False


def _ready_session_has_stale_live_observation_lag(
    output_root: Path,
    meeting_id: str,
    agent_ids: list[str],
    agents_by_id: dict[str, dict[str, object]],
    *,
    stale_after_seconds: float,
) -> bool:
    meeting_events = _live_agent_observation_events(output_root, meeting_id, {})
    for agent_id in agent_ids:
        latest_request = _latest_live_agent_turn_request_for_agent(meeting_events, agent_id)
        if not latest_request or not _event_is_stale_for_observation_restart(latest_request, stale_after_seconds):
            continue
        latest_request_id = _safe_session_run_health_identity(latest_request.get("id"))
        agent = agents_by_id.get(agent_id, {})
        status = _live_agent_live_observation_status(
            meeting_events,
            agent_id=agent_id,
            latest_request_id=latest_request_id,
            last_observed_live_event_id=_safe_session_run_health_identity(agent.get("last_observed_live_event_id")),
        )
        if status == "behind":
            return True
    return False


def _event_is_stale_for_observation_restart(event: dict[str, object], stale_after_seconds: float) -> bool:
    if stale_after_seconds <= 0:
        return False
    created_at = _parse_public_timestamp(event.get("created_at"))
    if created_at is None:
        return False
    return (datetime.now(UTC) - created_at).total_seconds() >= stale_after_seconds


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
    *,
    restart_count: int | None = None,
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
        restart_count=restart_count,
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


def live_agent_session_stop_agent_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
) -> dict[str, object]:
    return stop_live_agent_session_agent(
        output_root,
        process_supervisor,
        meeting_id=str(payload.get("meeting_id") or ""),
        group_id=str(payload.get("group_id") or ""),
        agent_id=str(payload.get("agent_id") or ""),
    )


def live_agent_session_agent_timing_payload(
    output_root: Path,
    payload: dict[str, object],
) -> dict[str, object]:
    agent_id = clean_lobby_text(payload.get("agent_id"), limit=64)
    if not agent_id:
        raise ValueError("Agent id is required.")
    if not any(str(agent.get("agent_id") or "") == agent_id for agent in read_live_agents(output_root)):
        raise ValueError(f"Live agent {agent_id} was not found.")

    live_agent_config_path = str(payload.get("live_agent_config_path") or payload.get("live_agent_config") or "").strip()
    config_result: dict[str, object] = {}
    if live_agent_config_path:
        config_result = update_live_agent_config_poll_interval(
            Path(live_agent_config_path),
            agent_id,
            payload.get("poll_interval"),
            payload.get("cooldown") if "cooldown" in payload else None,
        )
    agent = update_live_agent_poll_interval(output_root, agent_id, payload.get("poll_interval"))
    if "cooldown" in payload:
        agent = update_live_agent_cooldown(output_root, agent_id, payload.get("cooldown"))
    return {
        "status": "updated",
        "agent_id": agent_id,
        "poll_interval": agent.get("poll_interval"),
        "cooldown": agent.get("cooldown"),
        "config_path": str(config_result.get("config_path") or live_agent_config_path),
        "agent": agent,
    }


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
    redact_probe_events = _payload_bool(payload.get("redact_probe_events"))
    for agent_id in agent_ids:
        try:
            probe = _run_session_bound_agent_probe(
                output_root,
                agent_id,
                timeout_seconds=timeout_seconds,
                redact_events=redact_probe_events,
            )
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


def _run_session_bound_agent_probe(
    output_root: Path,
    agent_id: str,
    *,
    timeout_seconds: float,
    redact_events: bool = False,
) -> dict[str, object]:
    previous_engagement = _live_agent_engagement_snapshot(output_root, agent_id)
    previous_mode = str(previous_engagement.get("engagement_mode") or "")
    switch_for_probe = previous_mode in {"manual", "watch", "moderator_called"}
    if switch_for_probe:
        update_live_agent_engagement(output_root, agent_id, "human_only")
    try:
        result = run_live_agent_probe(output_root, agent_id, timeout_seconds=timeout_seconds)
    finally:
        if switch_for_probe:
            _restore_live_agent_engagement_snapshot(output_root, agent_id, previous_engagement)
    if redact_events:
        source_event_id = str(result.get("source_event_id") or "").strip()
        if source_event_id:
            result["redaction"] = _redact_real_session_smoke_lobby_events(output_root, [source_event_id])
    return result


def _redact_real_session_smoke_lobby_events(
    output_root: Path,
    source_event_ids: list[str],
) -> dict[str, object]:
    source_ids = {str(value or "").strip() for value in source_event_ids}
    source_ids.discard("")
    result = {"probe_event_count": 0, "reply_event_count": 0}
    if not source_ids:
        return result
    lobby_path = output_root / "lobby.jsonl"
    with LIVE_AGENT_LOBBY_LOCK:
        REAL_SESSION_SMOKE_REDACTED_SOURCE_EVENT_IDS.update(source_ids)
        if not lobby_path.exists():
            return result
        changed = False
        rewritten_lines: list[str] = []
        for line in lobby_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                rewritten_lines.append(line)
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                rewritten_lines.append(line)
                continue
            if not isinstance(event, dict):
                rewritten_lines.append(line)
                continue
            event_id = str(event.get("id") or "")
            source_event_id = str(event.get("source_event_id") or "")
            if event_id in source_ids:
                result["probe_event_count"] += 1
                if event.get("message") != REAL_SESSION_SMOKE_PROBE_REDACTION:
                    event["message"] = REAL_SESSION_SMOKE_PROBE_REDACTION
                    changed = True
            elif source_event_id in source_ids and event.get("live_agent_endpoint") is True:
                result["reply_event_count"] += 1
                if event.get("message") != REAL_SESSION_SMOKE_REPLY_REDACTION:
                    event["message"] = REAL_SESSION_SMOKE_REPLY_REDACTION
                    changed = True
            rewritten_lines.append(json.dumps(event, ensure_ascii=False, sort_keys=True))
        if changed:
            tmp_path = lobby_path.with_name(f"{lobby_path.name}.tmp")
            tmp_path.write_text("\n".join(rewritten_lines) + ("\n" if rewritten_lines else ""), encoding="utf-8")
            tmp_path.replace(lobby_path)
    return result


def _real_session_smoke_reply_message(source_event_id: str, message: str) -> str:
    if source_event_id and source_event_id in REAL_SESSION_SMOKE_REDACTED_SOURCE_EVENT_IDS:
        return REAL_SESSION_SMOKE_REPLY_REDACTION
    return message


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


def live_agent_join_brief_payload(payload: dict[str, object], *, default_server: str) -> dict[str, object]:
    return build_live_agent_join_brief(
        server=payload.get("server") or default_server,
        agent_id=payload.get("agent_id") or "",
        display_name=payload.get("display_name") or "",
        provider_kind=payload.get("provider_kind") or "manual",
        connection_kind=payload.get("connection_kind") or "manual",
        meeting_id=payload.get("meeting_id") or "",
        engagement_mode=payload.get("engagement_mode") or "mentioned",
        timeout=payload.get("timeout", 30.0),
        poll_interval=payload.get("poll_interval", DEFAULT_LIVE_AGENT_POLL_INTERVAL),
        max_chain_depth=payload.get("max_chain_depth", 1),
    )


def update_live_agent_engagement_payload(output_root: Path, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
    agent = update_live_agent_engagement(output_root, agent_id, str(payload.get("engagement_mode") or ""))
    return {"agent": agent, "agents": read_live_agents(output_root)}


def live_agent_room_payload(output_root: Path, agent_id: str) -> dict[str, object]:
    agent = _live_agent_for_id(output_root, agent_id)
    meeting_id = str(agent.get("meeting_id") or "").strip()
    live_events = []
    shared_memory: dict[str, object] = {}
    if meeting_id:
        meeting_dir = _safe_meeting_dir(output_root, meeting_id)
        if meeting_dir.exists():
            try:
                meeting = _read_meeting_record(meeting_dir)
            except (ValueError, OSError, json.JSONDecodeError):
                meeting = {}
            live_events = _live_events_with_projected_return_packets(
                read_live_events(meeting_dir),
                meeting_dir=meeting_dir,
                meeting=meeting,
                agent=agent,
            )
            shared_memory = load_live_meeting_memory_context(meeting_dir, meeting=meeting)
    return {
        "agent": agent,
        "agents": read_live_agents(output_root),
        "meetings": list_meetings(output_root),
        "meeting_id": meeting_id,
        "shared_memory": shared_memory,
        "live_events": live_events,
        "dm_events": read_live_agent_dm_events(
            output_root,
            str(agent.get("agent_id") or agent_id),
            after_event_id=clean_lobby_text(agent.get("last_observed_dm_event_id"), limit=128),
        ),
        "lobby_events": read_lobby(output_root, limit=LIVE_AGENT_ROOM_LOBBY_EVENT_LIMIT, meeting_id=meeting_id),
        "side_chat_events": read_side_chat(output_root),
    }


def room_friend_direct_dm_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    def resume_existing_group(group: dict[str, object]) -> object:
        session_payload = {
            "live_agent_config_path": str(group.get("config_path") or ""),
            "group_id": str(group.get("group_id") or ""),
            "meeting_id": str(group.get("meeting_id") or ""),
            "server": str(group.get("server") or default_server),
            "connect_timeout_seconds": _payload_nonnegative_float(payload.get("connect_timeout_seconds"), 5.0),
        }
        return live_agent_session_ensure_payload(
            output_root,
            process_supervisor,
            session_payload,
            default_server=default_server,
        )

    snapshot_groups = process_supervisor.snapshot_groups() if hasattr(process_supervisor, "snapshot_groups") else process_supervisor.list_groups()
    return enqueue_room_friend_direct_dm(
        output_root,
        payload,
        live_agents=read_live_agents(output_root),
        process_groups=snapshot_groups,
        resume_callback=resume_existing_group,
    )


def live_agent_return_packet_payload(
    output_root: Path,
    agent_id: str,
    *,
    meeting_id: str = "",
    source_event_id: str = "",
) -> dict[str, object]:
    agent = _live_agent_for_id(output_root, agent_id)
    clean_source_event_id = clean_lobby_text(source_event_id, limit=128)
    requested_meeting_id = clean_lobby_text(meeting_id, limit=128)
    agent_meeting_id = clean_lobby_text(agent.get("meeting_id"), limit=128)
    if not clean_source_event_id or not agent_meeting_id:
        raise ValueError("Return packet not found.")
    if requested_meeting_id and requested_meeting_id != agent_meeting_id:
        raise ValueError("Return packet not found.")
    clean_meeting_id = agent_meeting_id
    try:
        meeting_dir = _safe_meeting_dir(output_root, clean_meeting_id)
        meeting = _read_meeting_record(meeting_dir)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("Return packet not found.") from error
    candidate = _return_packet_read_candidate(
        meeting_dir,
        meeting=meeting,
        agent_id=clean_lobby_text(agent.get("agent_id"), limit=64),
        source_event_id=clean_source_event_id,
    )
    if candidate is None:
        raise ValueError("Return packet not found.")
    packet_path = candidate["packet_path"]
    packet_json_path = candidate["packet_json_path"]
    try:
        packet_markdown = packet_path.read_text(encoding="utf-8")
        packet_json = json.loads(packet_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Return packet not found.") from error
    return {
        "status": "ok",
        "agent_id": clean_lobby_text(agent.get("agent_id"), limit=64),
        "meeting_id": clean_meeting_id,
        "source_event_id": clean_source_event_id,
        "role_id": candidate["role_id"],
        "artifact_path": candidate["artifact_path"],
        "artifact_json_path": candidate["artifact_json_path"],
        "markdown": packet_markdown,
        "json": packet_json,
        "event": candidate["event"],
    }


def live_agent_heartbeat_payload(output_root: Path, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
    agent = heartbeat_live_agent(output_root, agent_id, status=str(payload.get("status") or "online"), metadata=payload)
    return {"agent": agent, "agents": read_live_agents(output_root)}


def live_agent_leave_payload(output_root: Path, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
    _live_agent_for_id(output_root, agent_id)
    metadata: dict[str, object] = {"last_error": ""}
    for key in ("last_observed_event_id", "last_observed_live_event_id", "last_observed_dm_event_id"):
        if key in payload:
            metadata[key] = payload.get(key)
    agent = heartbeat_live_agent(output_root, agent_id, status="offline", metadata=metadata)
    return {"agent": agent, "agents": read_live_agents(output_root)}


def live_agent_dm_reply_payload(output_root: Path, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
    response = append_live_agent_dm_reply(output_root, agent_id, payload)
    source_event_id = clean_lobby_text(payload.get("source_event_id"), limit=128)
    heartbeat_live_agent(
        output_root,
        agent_id,
        status="online",
        metadata={"last_observed_dm_event_id": source_event_id, "last_error": ""},
    )
    response["agent"] = _live_agent_for_id(output_root, agent_id)
    response["agents"] = read_live_agents(output_root)
    return response


def _history_page_limit(query: dict[str, list[str]]) -> int:
    try:
        return int(str(query.get("limit", [""])[0] or LOBBY_HISTORY_PAGE_LIMIT))
    except (TypeError, ValueError):
        return LOBBY_HISTORY_PAGE_LIMIT


def _pre_join_guide_payload(server_url: str) -> dict[str, object]:
    """Machine-readable join manual served from GET /join (Accept: application/json).

    Lets an AI client go from invite link to participation without downloading
    or reverse-engineering the SPA bundle.
    """
    base = (get_public_url() or server_url).rstrip("/")
    return {
        "service": "AgentsAssemble room",
        "how_to_join": {
            "request": f"POST {base}/api/room-invite/join",
            "json": {
                "invite_token": "<the token query parameter from this /join URL>",
                "display_name": "<your name in the room>",
                "participant_type": "human | agent",
                "device_token": "<generate one random string once, store it, reuse on every rejoin — keeps your identity stable>",
                "owner_display_name": "<for agents: the human you act for>",
            },
            "response": "session_token (Bearer) + guide(how_to/etiquette) — everything you need next is in that guide.",
        },
        "after_join": {
            "read_room": f"GET {base}/api/room/lobby (Authorization: Bearer <session_token>; snapshot — poll this)",
            "post_message": f"POST {base}/api/room/say {{\"message\": \"...\"}}",
            "leave": f"POST {base}/api/room-invite/leave",
            "warning": f"{base}/api/room/events is a server-sent-events stream, not JSON — it will hang plain HTTP clients.",
        },
        "api_catalog": f"GET {base}/api",
    }


def _api_catalog_payload(server_url: str) -> dict[str, object]:
    """Minimal API self-description (friend feedback #2: 403s everywhere told
    a new client nothing)."""
    base = (get_public_url() or server_url).rstrip("/")
    return {
        "service": "AgentsAssemble room API",
        "auth": {
            "guest": "Authorization: Bearer <session_token from /api/room-invite/join>",
            "host": "X-Host-Token header (host-only endpoints)",
        },
        "public_endpoints": {
            "pre_join_guide": f"GET {base}/join?format=json (or Accept: application/json)",
            "join": f"POST {base}/api/room-invite/join",
            "read_room": f"GET {base}/api/room/lobby?after=<event_id>",
            "events_sse": f"GET {base}/api/room/events (SSE stream)",
            "say": f"POST {base}/api/room/say",
            "leave": f"POST {base}/api/room-invite/leave",
            "companion_invite": f"POST {base}/api/room-invite/companion",
            "flow_status": f"GET {base}/api/live-agent-flow",
        },
        "notes": [
            "Send a stable device_token on join to keep one identity across rejoins.",
            "read_room supports ?after=<event_id> for incremental polling.",
        ],
    }


def _flow_turn_conflict(
    output_root: Path,
    *,
    actor_id: str,
    source_event_id: str,
    flow_id: str,
    flow_action: str,
    meeting_id: str,
    message: str,
) -> str:
    """Serialize Play-Mode speech so concurrent agents can't double-take a turn.

    Returns "" (allowed), "turn_conflict" (someone else already spoke after the
    poster's source event — re-read the room and regenerate), or
    "duplicate_flow_message" (identical to the latest flow speech, e.g. two
    agents answering a word-chain prompt with the same word).
    """
    if not flow_id or flow_action not in FLOW_SPEAKING_ACTIONS:
        return ""
    events = read_lobby(output_root, meeting_id=meeting_id)
    flow_policy = ""
    for event in events:
        if str(event.get("flow_id") or "") == flow_id and str(event.get("flow_event_type") or "") == "started":
            flow_policy = str(event.get("flow_policy") or "")
    normalized_message = " ".join(str(message or "").split()).casefold()
    last_speaking: dict[str, object] | None = None
    for event in reversed(events):
        if str(event.get("flow_id") or "") == flow_id and str(event.get("flow_action") or "") in FLOW_SPEAKING_ACTIONS:
            last_speaking = event
            break
    if (
        last_speaking is not None
        and normalized_message
        and str(last_speaking.get("actor_id") or "") != actor_id
        and " ".join(str(last_speaking.get("message") or "").split()).casefold() == normalized_message
    ):
        return "duplicate_flow_message"
    if flow_policy not in {"turn_based_floor", "natural", "round_robin"}:
        return ""
    # CAS: reject when another participant's flow speech landed after the
    # source event this reply was generated from (the poster saw a stale room).
    seen_source = not source_event_id
    for event in events:
        if not seen_source:
            if str(event.get("id") or "") == source_event_id:
                seen_source = True
            continue
        if str(event.get("flow_id") or "") != flow_id:
            continue
        if str(event.get("flow_action") or "") not in FLOW_SPEAKING_ACTIONS:
            continue
        if str(event.get("actor_id") or "") == actor_id:
            continue
        return "turn_conflict"
    return ""


def live_agent_lobby_message_payload(output_root: Path, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
    agent = heartbeat_live_agent(output_root, agent_id, status="online")
    message = str(payload.get("message") or "").strip()
    if not message:
        raise ValueError("Message is required.")
    actor_id = str(agent.get("agent_id") or agent_id)
    if is_room_member_muted(output_root, clean_lobby_text(agent.get("meeting_id"), limit=128), actor_id):
        raise ValueError("This participant is muted by the room host.")
    source_event_id = clean_lobby_text(payload.get("source_event_id"), limit=128)
    with LIVE_AGENT_LOBBY_LOCK:
        existing_event = _existing_live_agent_lobby_reply(output_root, actor_id=actor_id, source_event_id=source_event_id)
        if existing_event is not None:
            if (
                source_event_id
                and source_event_id in REAL_SESSION_SMOKE_REDACTED_SOURCE_EVENT_IDS
                and existing_event.get("message") != REAL_SESSION_SMOKE_REPLY_REDACTION
            ):
                _redact_real_session_smoke_lobby_events(output_root, [source_event_id])
                existing_event = _existing_live_agent_lobby_reply(
                    output_root,
                    actor_id=actor_id,
                    source_event_id=source_event_id,
                ) or existing_event
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
        flow_metadata = _live_agent_lobby_flow_metadata(payload)
        agent_meeting_id = clean_lobby_text(agent.get("meeting_id"), limit=128)
        if agent_meeting_id:
            flow_metadata["flow_meeting_id"] = agent_meeting_id
        conflict = _flow_turn_conflict(
            output_root,
            actor_id=actor_id,
            source_event_id=source_event_id,
            flow_id=str(flow_metadata.get("flow_id") or ""),
            flow_action=str(flow_metadata.get("flow_action") or ""),
            meeting_id=str(flow_metadata.get("flow_meeting_id") or ""),
            message=message,
        )
        if conflict:
            updated_agent = heartbeat_live_agent(
                output_root,
                actor_id,
                status="online",
                metadata={"last_observed_event_id": source_event_id},
            )
            return {"status": conflict, "agent": updated_agent, "events": read_lobby(output_root)}
        event = append_lobby_event(
            output_root,
            {
                "name": agent.get("display_name") or agent.get("agent_id") or agent_id,
                "side": "other-agent",
                "kind": payload.get("kind") or "message",
                "message": _real_session_smoke_reply_message(source_event_id, message),
                "actor_id": actor_id,
                "actor_type": "agent",
                "source_event_id": source_event_id,
                "auto_chain_depth": payload.get("auto_chain_depth") or 0,
                **flow_metadata,
            },
            live_agent_endpoint=True,
            allow_flow_metadata=True,
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


def _live_agent_lobby_flow_metadata(payload: dict[str, object]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for key in (
        "target_agent_id",
        "flow_id",
        "flow_meeting_id",
        "flow_action",
        "flow_reason",
        "flow_runtime_mode",
        "flow_turn_delivery_ms",
        "flow_provider_invocation_ms",
        "flow_reply_post_ms",
    ):
        if key in payload:
            metadata[key] = payload.get(key)
    if "flow_reply_post_ms" not in metadata and payload.get("flow_reply_post_started_at"):
        metadata["flow_reply_post_ms"] = _flow_reply_post_elapsed_ms(payload.get("flow_reply_post_started_at"))
    return metadata


def _flow_reply_post_elapsed_ms(value: object) -> int:
    text = clean_lobby_text(value, limit=128)
    if not text:
        return 0
    try:
        started_at = datetime.fromisoformat(text)
    except ValueError:
        return 0
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    return max(0, int(round((datetime.now(UTC) - started_at.astimezone(UTC)).total_seconds() * 1000)))


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
    cancelled_count = sum(1 for result in results if result["status"] == "cancelled")
    return {
        "status": _live_agent_turn_sequence_status(
            answered_count,
            timeout_count,
            skipped_count,
            cancelled_count,
            turn_count=len(turns),
        ),
        "meeting_id": clean_meeting_id,
        "turn_count": len(turns),
        "answered_count": answered_count,
        "timeout_count": timeout_count,
        "skipped_count": skipped_count,
        "cancelled_count": cancelled_count,
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
    cancelled_count = sum(1 for result in results if result["status"] == "cancelled")
    checkpoint = {
        "status": _live_agent_turn_sequence_status(
            answered_count,
            timeout_count,
            skipped_count,
            cancelled_count,
            turn_count=len(target_agent_ids),
        ),
        "checkpoint_id": checkpoint_id,
        "meeting_id": clean_meeting_id,
        "group_id": group_id,
        "turn_count": len(target_agent_ids),
        "answered_count": answered_count,
        "timeout_count": timeout_count,
        "skipped_count": skipped_count,
        "cancelled_count": cancelled_count,
        "timeout_seconds": timeout_seconds,
        "agent_ids": target_agent_ids,
        "results": results,
        "readiness": readiness,
    }
    artifacts = write_review_checkpoint_artifacts(meeting_dir, checkpoint)
    checkpoint.update(artifacts)
    append_live_event(
        meeting_dir,
        {
            "kind": "artifact",
            "meeting_id": clean_meeting_id,
            "channel": "review",
            "official_record": False,
            "artifact_kind": "review_checkpoint",
            "artifact_path": artifacts["artifact_path"],
            "artifact_json_path": artifacts["artifact_json_path"],
            "review_checkpoint_id": checkpoint_id,
            "content": f"Review checkpoint artifact ready: {artifacts['artifact_path']}",
        },
    )
    return checkpoint


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
        if official_turn_cancellation(read_live_events(meeting_dir, limit=None), agent_id=agent_id, source_event_id=source_event_id):
            raise ValueError("Official turn request was cancelled.")
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
        shared_memory = _refresh_live_meeting_memory_after_official_reply(meeting_dir, event)
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
        "shared_memory": shared_memory,
        "live_events": _live_events_visible_to_agent(read_live_events(meeting_dir), agent_id),
    }


def _refresh_live_meeting_memory_after_official_reply(
    meeting_dir: Path,
    event: dict[str, object],
) -> dict[str, object]:
    if event.get("official_record") is not True or event.get("channel") != "official":
        return {}
    try:
        meeting = _read_meeting_record(meeting_dir)
        can_update_live_state = True
    except (ValueError, OSError, json.JSONDecodeError):
        meeting = {
            "meeting_id": clean_lobby_text(event.get("meeting_id"), limit=128),
            "topic": clean_lobby_text(event.get("meeting_id"), limit=240),
        }
        can_update_live_state = False
    memory = write_live_meeting_memory_artifacts(meeting_dir, meeting=meeting)
    if can_update_live_state:
        meeting["shared_memory"] = memory
        write_live_state(meeting_dir, meeting)
    return _shared_memory_operation_details(memory)


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
    approved_agents = _safe_payload_strings(payload.get("approved_agents"), limit=64)
    approved_commands = _safe_payload_strings(payload.get("approved_commands"), limit=64)
    if approved_agents or approved_commands:
        apply_discovery_approval_filter(report, approved_agents=approved_agents, approved_commands=approved_commands)
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


def live_agent_real_session_smoke_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    return run_live_agent_real_session_smoke(
        server=default_server,
        group_id=str(payload.get("group_id") or ""),
        meeting_id=str(payload.get("meeting_id") or ""),
        live_agent_config_path=str(payload.get("live_agent_config_path") or payload.get("live_agent_config") or ""),
        council_config_path=str(payload.get("council_config_path") or payload.get("council_config") or ""),
        agent_config_path=str(payload.get("agent_config_path") or payload.get("agent_config") or ""),
        timeout_seconds=_payload_nonnegative_float(payload.get("timeout"), 12.0),
        approve_real_providers=_payload_bool(payload.get("approve_real_providers")),
        official_round_smoke=_payload_bool(payload.get("official_round_smoke")),
        restart_smoke=_payload_bool(payload.get("restart_smoke")),
        request_json=_request_json,
        output_root=output_root,
    )


def live_agent_readiness_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
    session_run_monitor: LiveAgentSessionRunMonitor | None = None,
) -> dict[str, object]:
    health = live_agent_health_payload(output_root, process_supervisor, session_run_monitor=session_run_monitor)
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


def live_agent_health_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    *,
    session_run_monitor: LiveAgentSessionRunMonitor | None = None,
) -> dict[str, object]:
    agents = read_live_agents(output_root)
    groups = process_supervisor.snapshot_groups()
    diagnostic_group_ids = _diagnostic_agent_group_ids(agents)
    agent_summary = _live_agent_health_summary(agents)
    admission_summary = _live_agent_admission_health_summary(output_root, agents)
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
    observation_summary = _live_agent_observation_health_summary(
        output_root,
        groups,
        agents,
        session_summary,
        diagnostic_group_ids=diagnostic_group_ids,
    )
    sandbox_enforcement_summary = _live_agent_sandbox_enforcement_health_summary(agents)
    process_monitor_summary = _live_agent_process_monitor_health_summary(process_supervisor)
    process_monitor_attention = (
        process_monitor_summary.get("attention")
        if isinstance(process_monitor_summary.get("attention"), list)
        else []
    )
    shared_memory_summary = _live_agent_shared_memory_health_summary(output_root, session_summary)
    shared_memory_attention = (
        shared_memory_summary.get("attention")
        if isinstance(shared_memory_summary.get("attention"), list)
        else []
    )
    session_run_summary = _live_agent_session_run_health_summary(output_root, session_summary=session_summary)
    session_run_monitor_summary = _live_agent_session_run_monitor_health_summary(session_run_monitor)
    session_run_monitor_attention = (
        session_run_monitor_summary.get("attention")
        if isinstance(session_run_monitor_summary.get("attention"), list)
        else []
    )
    sandbox_enforcement_attention = (
        sandbox_enforcement_summary.get("attention")
        if isinstance(sandbox_enforcement_summary.get("attention"), list)
        else []
    )
    status = (
        "degraded"
        if agent_summary["attention"]
        or process_summary["attention"]
        or process_monitor_attention
        or connection_summary["attention"]
        or session_summary["attention"]
        or observation_summary["attention"]
        or sandbox_enforcement_attention
        or shared_memory_attention
        or session_run_summary["attention"]
        or session_run_monitor_attention
        else "ok"
    )
    payload = {
        "status": status,
        "agents": agent_summary,
        "admission": admission_summary,
        "processes": process_summary,
        "connections": connection_summary,
        "sessions": session_summary,
        "observations": observation_summary,
        "sandbox_enforcement": sandbox_enforcement_summary,
        "shared_memory": shared_memory_summary,
        "session_runs": session_run_summary,
    }
    if process_monitor_summary:
        payload["process_monitor"] = process_monitor_summary
    if session_run_monitor_summary:
        payload["session_run_monitor"] = session_run_monitor_summary
    return payload


def local_resource_snapshot_payload(process_supervisor: LiveAgentProcessSupervisor) -> dict[str, object]:
    return cached_local_resource_snapshot(supervised_pids=_live_agent_supervised_pids(process_supervisor))


def _live_agent_supervised_pids(process_supervisor: LiveAgentProcessSupervisor) -> set[int]:
    pids: set[int] = set()
    try:
        groups = process_supervisor.snapshot_groups()
    except Exception:
        return pids
    for group in _as_dict_list(groups):
        status = str(group.get("status") or "")
        if status not in {"running", "restarting"}:
            continue
        pid = _safe_positive_int(group.get("pid"))
        if pid is not None:
            pids.add(pid)
    return pids


def _safe_positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _live_agent_admission_health_summary(output_root: Path, agents: list[dict[str, object]]) -> dict[str, object]:
    visible_agents = [agent for agent in agents if not _is_diagnostic_agent(agent)]
    safe_payload = safe_live_agent_roster_payload(
        _live_agent_roster_with_admission_evidence(output_root, {"agents": visible_agents})
    )
    safe_agents = _as_dict_list(safe_payload.get("agents"))
    counts = {status: 0 for status in LIVE_AGENT_ADMISSION_HEALTH_STATUSES}
    host_approved = 0
    attention: list[str] = []
    for index, agent in enumerate(safe_agents, start=1):
        status = str(agent.get("admission_status") or "")
        if status not in counts:
            status = "unknown"
        counts[status] += 1
        if agent.get("host_approved_binding") is True:
            host_approved += 1
            continue
        meeting_id = _safe_session_run_health_identity(agent.get("meeting_id")) or "lobby"
        agent_id = _safe_session_run_health_identity(agent.get("agent_id")) or f"missing-agent-id-{index}"
        attention.append(f"{meeting_id}:{agent_id}:{status}")
    return {
        "total": len(safe_agents),
        "host_approved": host_approved,
        "unapproved": len(safe_agents) - host_approved,
        "counts": counts,
        "attention": attention,
    }


def _live_agent_sandbox_enforcement_health_summary(agents: list[dict[str, object]]) -> dict[str, object]:
    safe_payload = safe_live_agent_roster_payload({"agents": [agent for agent in agents if not _is_diagnostic_agent(agent)]})
    safe_agents = _as_dict_list(safe_payload.get("agents"))
    counts = {"advisory": 0, "codex_readonly": 0, "os_sandboxed": 0, "unknown": 0}
    attention = []
    for index, agent in enumerate(safe_agents, start=1):
        enforcement = str(agent.get("sandbox_enforcement") or "")
        if enforcement not in counts:
            enforcement = "unknown"
        counts[enforcement] += 1
        if enforcement == "unknown":
            agent_id = _safe_session_run_health_identity(agent.get("agent_id")) or f"missing-agent-id-{index}"
            attention.append(agent_id)
    return {"counts": counts, "attention": attention}


def _live_agent_shared_memory_health_summary(
    output_root: Path,
    session_summary: dict[str, object],
) -> dict[str, object]:
    ready_sessions = 0
    items: list[dict[str, object]] = []
    latest_event_id = ""
    official_event_count = 0
    open_question_count = 0
    action_item_count = 0
    decision_count = 0
    attention: list[str] = []
    for session in _as_dict_list(session_summary.get("items")):
        if str(session.get("status") or "") != "ready":
            continue
        ready_sessions += 1
        meeting_id = _safe_session_run_health_identity(session.get("meeting_id"))
        group_id = _safe_session_run_health_identity(session.get("group_id"))
        if not meeting_id or not group_id:
            continue
        meeting_dir = output_root / "meetings" / meeting_id
        try:
            meeting = _read_live_agent_health_meeting(meeting_dir)
            memory = build_live_meeting_memory(read_live_events(meeting_dir, limit=None), meeting=meeting)
        except Exception:
            attention.append(f"{meeting_id}:{group_id}:memory_unavailable")
            continue
        item = _live_agent_shared_memory_health_item(memory, meeting_id=meeting_id, group_id=group_id)
        if not item:
            continue
        items.append(item)
        official_event_count += int(item["official_event_count"])
        open_question_count += int(item["open_question_count"])
        action_item_count += int(item["action_item_count"])
        decision_count += int(item["decision_count"])
        latest_event_id = str(item.get("last_official_event_id") or latest_event_id)
    return {
        "ready_sessions": ready_sessions,
        "with_memory": len(items),
        "official_event_count": official_event_count,
        "decision_count": decision_count,
        "open_question_count": open_question_count,
        "action_item_count": action_item_count,
        "last_official_event_id": latest_event_id,
        "attention": attention,
        "items": items,
    }


def _live_agent_shared_memory_health_item(
    memory: dict[str, object],
    *,
    meeting_id: str,
    group_id: str,
) -> dict[str, object]:
    official_event_count = _payload_nonnegative_int(memory.get("official_event_count"), 0)
    if official_event_count <= 0:
        return {}
    return {
        "meeting_id": meeting_id,
        "group_id": group_id,
        "official_event_count": official_event_count,
        "official_message_count": _payload_nonnegative_int(memory.get("official_message_count"), 0),
        "official_synthesis_count": _payload_nonnegative_int(memory.get("official_synthesis_count"), 0),
        "decision_count": _payload_nonnegative_int(memory.get("decision_count"), _memory_item_count(memory.get("decisions"))),
        "open_question_count": _payload_nonnegative_int(
            memory.get("open_question_count"),
            _memory_item_count(memory.get("open_questions")),
        ),
        "action_item_count": _payload_nonnegative_int(
            memory.get("action_item_count"),
            _memory_item_count(memory.get("action_items")),
        ),
        "last_official_event_id": _safe_session_run_health_identity(memory.get("last_official_event_id")),
    }


def _read_live_agent_health_meeting(meeting_dir: Path) -> dict[str, object]:
    try:
        value = json.loads((meeting_dir / "meeting.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _live_agent_health_summary(agents: list[dict[str, object]]) -> dict[str, object]:
    agents = [agent for agent in agents if not _is_diagnostic_agent(agent)]
    counts = {"online": 0, "working": 0, "error": 0, "stale": 0, "offline": 0}
    attention = []
    for index, agent in enumerate(agents, start=1):
        raw_status = str(agent.get("status") or "offline")
        status = raw_status if raw_status in counts else "offline"
        counts[status] += 1
        if status in {"error", "stale", "offline"}:
            attention.append(_safe_session_run_health_identity(agent.get("agent_id")) or f"missing-agent-id-{index}")
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
        group_id = _safe_process_group_id(group.get("group_id"), fallback=f"missing-process-group-id-{index}")
        meeting_id = _safe_process_meeting_id(group.get("meeting_id"))
        if group_id and meeting_id:
            meeting_ids[group_id] = meeting_id
        if status in {"restarting", "error", "unknown", "stopped"}:
            attention.append(group_id)
            reason = _live_agent_process_health_reason(group)
            if reason:
                reasons[group_id] = reason
    return {"total": len(groups), "counts": counts, "attention": attention, "meeting_ids": meeting_ids, "reasons": reasons}


def _live_agent_process_monitor_health_summary(process_supervisor: LiveAgentProcessSupervisor) -> dict[str, object]:
    snapshot_fn = getattr(process_supervisor, "monitor_snapshot", None)
    if not callable(snapshot_fn):
        return {}
    try:
        raw = snapshot_fn()
    except Exception as error:
        raw = {
            "running": False,
            "interval_seconds": 0,
            "last_tick_at": "",
            "last_status": "failed",
            "last_group_count": 0,
            "last_error_type": _safe_session_run_monitor_error_type(error),
        }
    last_status = _safe_monitor_health_status(raw.get("last_status"))
    last_error_type = _safe_monitor_health_error_type(raw.get("last_error_type"))
    attention = []
    if last_status == "failed":
        attention.append(f"failed:{last_error_type or 'Exception'}")
    return {
        "running": raw.get("running") is True,
        "interval_seconds": _safe_process_monitor_interval_value(raw.get("interval_seconds")),
        "last_tick_at": _safe_session_run_health_timestamp(raw.get("last_tick_at")),
        "last_status": last_status,
        "last_group_count": _safe_session_run_health_int(raw.get("last_group_count")),
        "last_error_type": last_error_type,
        "attention": attention,
    }


def _safe_monitor_health_status(value: object) -> str:
    status = clean_lobby_text(value, limit=64)
    return status if status in {"not_started", "ok", "failed"} else "unknown"


def _safe_monitor_health_error_type(value: object) -> str:
    error_type = clean_lobby_text(value, limit=80)
    return error_type if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,79}", error_type) else ""


def _safe_process_monitor_interval_value(value: object) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(seconds):
        return 0.0
    return max(0.01, seconds)


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
    return _looks_sensitive_session_run_health_text(reason) or "/" in reason or "\\" in reason or ".json" in lowered or "env:" in lowered


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
        group_id = _safe_agent_connection_identity(group.get("group_id"))
        for item in _as_dict_list(group_connection.get("attention")):
            agent_id = _safe_agent_connection_identity(item.get("agent_id"))
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


def _live_agent_observation_health_summary(
    output_root: Path,
    groups: list[dict[str, object]],
    agents: list[dict[str, object]],
    session_summary: dict[str, object],
    *,
    diagnostic_group_ids: set[str] | None = None,
) -> dict[str, object]:
    diagnostic_group_ids = diagnostic_group_ids or set()
    agents_by_id = {
        _safe_session_run_health_identity(agent.get("agent_id")): agent
        for agent in agents
        if _safe_session_run_health_identity(agent.get("agent_id")) and not _is_diagnostic_agent(agent)
    }
    groups_by_session = {
        (
            _safe_session_run_health_identity(group.get("meeting_id")),
            _safe_session_run_health_identity(group.get("group_id")),
        ): group
        for group in groups
        if not _is_diagnostic_process_group(group, diagnostic_group_ids)
    }
    latest_lobby_event = _latest_lobby_event(output_root)
    latest_lobby_event_id = _safe_session_run_health_identity(latest_lobby_event.get("id")) if latest_lobby_event else ""
    latest_lobby_actor_id = _safe_session_run_health_identity(latest_lobby_event.get("actor_id")) if latest_lobby_event else ""
    events_by_meeting: dict[str, list[dict[str, object]]] = {}
    items: list[dict[str, object]] = []
    attention: list[str] = []
    lobby_behind_count = 0
    live_behind_count = 0
    error_count = 0
    latest_live_request_count = 0

    for session_item in _as_dict_list(session_summary.get("items")):
        if str(session_item.get("status") or "") != "ready":
            continue
        meeting_id = _safe_session_run_health_identity(session_item.get("meeting_id"))
        group_id = _safe_session_run_health_identity(session_item.get("group_id"))
        if not meeting_id or not group_id:
            continue
        group = groups_by_session.get((meeting_id, group_id))
        if group is None:
            continue
        meeting_events = _live_agent_observation_events(output_root, meeting_id, events_by_meeting)
        for manifest_agent in _as_dict_list(group.get("agents")):
            agent_id = _safe_session_run_health_identity(manifest_agent.get("agent_id"))
            if not agent_id:
                continue
            agent = agents_by_id.get(agent_id, {})
            latest_request = _latest_live_agent_turn_request_for_agent(meeting_events, agent_id)
            if latest_request:
                latest_live_request_count += 1
            item = _live_agent_observation_item(
                agent,
                meeting_id=meeting_id,
                group_id=group_id,
                agent_id=agent_id,
                latest_lobby_event_id=latest_lobby_event_id,
                latest_lobby_actor_id=latest_lobby_actor_id,
                latest_live_request=latest_request,
                meeting_events=meeting_events,
            )
            if item["lobby_status"] == "behind":
                lobby_behind_count += 1
                attention.append(f"{meeting_id}:{group_id}:{agent_id}:lobby_cursor_behind")
            if item["live_status"] == "behind":
                live_behind_count += 1
                attention.append(f"{meeting_id}:{group_id}:{agent_id}:live_cursor_behind")
            if _live_agent_observation_has_active_error(agent):
                error_count += 1
            items.append(item)

    return {
        "ready_agent_count": len(items),
        "lobby_behind_count": lobby_behind_count,
        "live_behind_count": live_behind_count,
        "error_count": error_count,
        "latest_lobby_event_id": latest_lobby_event_id,
        "latest_live_request_count": latest_live_request_count,
        "attention": attention,
        "items": items,
    }


def _latest_lobby_event(output_root: Path) -> dict[str, object]:
    events = read_lobby(output_root, limit=1)
    return events[-1] if events else {}


def _live_agent_observation_events(
    output_root: Path,
    meeting_id: str,
    events_by_meeting: dict[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    if meeting_id not in events_by_meeting:
        try:
            events_by_meeting[meeting_id] = read_live_events(_safe_meeting_dir(output_root, meeting_id), limit=200)
        except ValueError:
            events_by_meeting[meeting_id] = []
    return events_by_meeting[meeting_id]


def _latest_live_agent_turn_request_for_agent(
    events: list[dict[str, object]],
    agent_id: str,
) -> dict[str, object]:
    for event in reversed(events):
        if event.get("kind") != "live_agent_turn_request":
            continue
        if _safe_session_run_health_identity(event.get("target_agent_id")) != agent_id:
            continue
        return event
    return {}


def _live_agent_observation_item(
    agent: dict[str, object],
    *,
    meeting_id: str,
    group_id: str,
    agent_id: str,
    latest_lobby_event_id: str,
    latest_lobby_actor_id: str,
    latest_live_request: dict[str, object],
    meeting_events: list[dict[str, object]],
) -> dict[str, object]:
    last_observed_lobby = _safe_session_run_health_identity(agent.get("last_observed_event_id"))
    last_observed_live = _safe_session_run_health_identity(agent.get("last_observed_live_event_id"))
    latest_live_event_id = _safe_session_run_health_identity(latest_live_request.get("id"))
    return {
        "meeting_id": meeting_id,
        "group_id": group_id,
        "agent_id": agent_id,
        "lobby_status": _live_agent_lobby_observation_status(
            latest_lobby_event_id,
            last_observed_lobby,
            latest_actor_id=latest_lobby_actor_id,
            agent_id=agent_id,
        ),
        "live_status": _live_agent_live_observation_status(
            meeting_events,
            agent_id=agent_id,
            latest_request_id=latest_live_event_id,
            last_observed_live_event_id=last_observed_live,
        ),
        "latest_lobby_event_id": latest_lobby_event_id,
        "latest_live_event_id": latest_live_event_id,
        "last_observed_event_id": last_observed_lobby,
        "last_observed_live_event_id": last_observed_live,
        "last_reply_at": _safe_session_run_health_timestamp(agent.get("last_reply_at")),
    }


def _live_agent_lobby_observation_status(
    latest_event_id: str,
    last_observed_event_id: str,
    *,
    latest_actor_id: str,
    agent_id: str,
) -> str:
    if not latest_event_id:
        return "none"
    if latest_actor_id and latest_actor_id == agent_id:
        return "self"
    return "current" if last_observed_event_id == latest_event_id else "behind"


def _live_agent_live_observation_status(
    events: list[dict[str, object]],
    *,
    agent_id: str,
    latest_request_id: str,
    last_observed_live_event_id: str,
) -> str:
    if not latest_request_id:
        return "none"
    terminal_status = _live_agent_turn_terminal_status_in_events(events, agent_id=agent_id, source_event_id=latest_request_id)
    if terminal_status:
        return terminal_status
    event_index = _live_event_index_by_id(events)
    latest_index = event_index.get(latest_request_id)
    observed_index = event_index.get(last_observed_live_event_id)
    if latest_index is not None and observed_index is not None and observed_index >= latest_index:
        return "current"
    return "current" if last_observed_live_event_id == latest_request_id else "behind"


def _live_agent_turn_terminal_status_in_events(
    events: list[dict[str, object]],
    *,
    agent_id: str,
    source_event_id: str,
) -> str:
    for event in events:
        if is_official_turn_cancellation_event(event):
            if _safe_session_run_health_identity(event.get("target_agent_id")) != agent_id:
                continue
            if _safe_session_run_health_identity(event.get("source_event_id")) == source_event_id:
                return "cancelled"
            continue
        if not is_official_turn_reply_event(event) and not is_review_checkpoint_reply_event(event):
            continue
        if _safe_session_run_health_identity(event.get("actor_id")) != agent_id:
            continue
        if _safe_session_run_health_identity(event.get("source_event_id")) == source_event_id:
            return "answered"
    return ""


def _live_agent_reply_for_request_in_events(
    events: list[dict[str, object]],
    *,
    agent_id: str,
    source_event_id: str,
) -> bool:
    for event in events:
        if not is_official_turn_reply_event(event) and not is_review_checkpoint_reply_event(event):
            continue
        if _safe_session_run_health_identity(event.get("actor_id")) != agent_id:
            continue
        if _safe_session_run_health_identity(event.get("source_event_id")) == source_event_id:
            return True
    return False


def _live_event_index_by_id(events: list[dict[str, object]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for index, event in enumerate(events):
        event_id = _safe_session_run_health_identity(event.get("id"))
        if event_id:
            result[event_id] = index
    return result


def _live_agent_observation_has_active_error(agent: dict[str, object]) -> bool:
    return clean_lobby_text(agent.get("status"), limit=64) == "error"


def _live_agent_session_run_health_summary(
    output_root: Path,
    *,
    session_summary: dict[str, object] | None = None,
) -> dict[str, object]:
    snapshot = LiveAgentSessionRunController(output_root).health_snapshot()
    runs = snapshot.get("runs") if isinstance(snapshot.get("runs"), list) else []
    readiness_by_target = _session_readiness_by_target(session_summary or {})
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
        readiness = _session_run_readiness_overlay(run, readiness_by_target) if active else {}
        readiness_issue = _live_agent_session_run_readiness_issue(readiness) if active and status == "ready" else ""
        if active and status != "ready":
            attention.append(_live_agent_session_run_attention_label(run, status=status, retrying=retrying))
        elif readiness_issue:
            attention.append(_live_agent_session_run_attention_label(run, status=status, reason=readiness_issue))
        item = {
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
        if active:
            item["readiness"] = readiness
        items.append(item)
    return {
        "total": _safe_session_run_health_int(snapshot.get("total")),
        "active": _safe_session_run_health_int(snapshot.get("active")),
        "ready": _safe_session_run_health_int(snapshot.get("ready")),
        "retrying": retrying_count,
        "attention": attention,
        "items": items,
    }


def _live_agent_session_run_monitor_health_summary(
    monitor: LiveAgentSessionRunMonitor | None,
) -> dict[str, object]:
    if monitor is None:
        return {}
    raw = monitor.snapshot()
    last_status = _safe_session_run_monitor_status(raw.get("last_status"))
    last_error_type = _safe_session_run_monitor_error_type_value(raw.get("last_error_type"))
    attention = []
    if last_status == "failed":
        attention.append(f"failed:{last_error_type or 'Exception'}")
    return {
        "running": raw.get("running") is True,
        "interval_seconds": _safe_session_run_monitor_interval_value(raw.get("interval_seconds")),
        "last_tick_at": _safe_session_run_health_timestamp(raw.get("last_tick_at")),
        "last_status": last_status,
        "last_result_count": _safe_session_run_health_int(raw.get("last_result_count")),
        "last_error_type": last_error_type,
        "attention": attention,
    }


def _safe_session_run_monitor_status(value: object) -> str:
    status = clean_lobby_text(value, limit=64)
    return status if status in {"not_started", "ok", "degraded", "failed"} else "unknown"


def _safe_session_run_monitor_error_type_value(value: object) -> str:
    error_type = clean_lobby_text(value, limit=80)
    return error_type if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,79}", error_type) else ""


def _safe_session_run_monitor_interval_value(value: object) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_SESSION_RUN_MONITOR_INTERVAL_SECONDS
    if not math.isfinite(seconds):
        return DEFAULT_SESSION_RUN_MONITOR_INTERVAL_SECONDS
    return max(MIN_SESSION_RUN_MONITOR_INTERVAL_SECONDS, seconds)


def _live_agent_session_run_retrying(run: dict[str, object]) -> bool:
    return (
        _safe_session_run_health_int(run.get("reconcile_failure_count")) > 0
        or _safe_session_run_health_int(run.get("reconcile_backoff_seconds")) > 0
        or bool(_safe_session_run_health_timestamp(run.get("next_reconcile_at")))
    )


def _live_agent_session_run_readiness_issue(readiness: dict[str, object]) -> str:
    if clean_lobby_text(readiness.get("status"), limit=64) == "ready":
        return ""
    attention = readiness.get("attention") if isinstance(readiness.get("attention"), list) else []
    if "session_run:no_current_readiness" in attention:
        return "no_current_readiness"
    if "session_run:missing_target" in attention:
        return "missing_target"
    process_status = clean_lobby_text(readiness.get("process_status"), limit=64)
    if process_status and process_status != "running":
        return f"process_{process_status}"
    return "current_readiness_degraded"


def _live_agent_session_run_attention_label(
    run: dict[str, object],
    *,
    status: str,
    retrying: bool = False,
    reason: str = "",
) -> str:
    parts = [
        _safe_session_run_health_identity(run.get("meeting_id")) or "-",
        _safe_session_run_health_identity(run.get("group_id")) or "-",
        _safe_session_run_health_identity(run.get("run_id")) or "-",
        clean_lobby_text(status, limit=64) or "unknown",
    ]
    if reason:
        parts.append(_safe_session_run_health_reason(reason))
    elif retrying:
        parts.append("retrying")
    return ":".join(parts)


def _safe_session_run_health_reason(value: object) -> str:
    reason = clean_lobby_text(value, limit=64)
    if not reason or _looks_sensitive_session_run_health_text(reason):
        return "current_readiness_degraded"
    return reason if re.fullmatch(r"[A-Za-z0-9_:-]{1,64}", reason) else "current_readiness_degraded"


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
    return _safe_session_run_health_identity(value)


def _safe_process_group_id(value: object, *, fallback: str) -> str:
    return _safe_session_run_health_identity(value) or fallback


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


def _safe_agent_connection_identity(value: object) -> str:
    return _safe_session_run_health_identity(value) or "unknown"


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
        compatibility_attention = _manifest_agent_connection_attention(agent, manifest_agent)
        if compatibility_attention:
            attention.append({"agent_id": agent_id, "status": compatibility_attention})
            continue
        status = str(agent.get("status") or "offline")
        if status in {"online", "working"}:
            connected += 1
            continue
        if status not in {"error", "stale", "offline"}:
            status = "offline"
        attention.append({"agent_id": agent_id, "status": status})
    return {"expected": expected, "connected": connected, "attention": attention}


def _manifest_agent_connection_attention(agent: dict[str, object], manifest_agent: dict[str, object]) -> str:
    provider_kind = clean_lobby_text(manifest_agent.get("provider_kind"), limit=64)
    if provider_kind and clean_lobby_text(agent.get("provider_kind"), limit=64) != provider_kind:
        return "provider_kind_mismatch"
    connection_kind = clean_lobby_text(manifest_agent.get("connection_kind"), limit=64)
    if connection_kind and clean_lobby_text(agent.get("connection_kind"), limit=64) != connection_kind:
        return "connection_kind_mismatch"
    return ""


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
            engagement_mode=str(payload.get("engagement_mode") or "moderator_called"),
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


def _live_agent_register_operation_details(
    output_root: Path,
    agent: dict[str, object],
    *,
    clean_agent_id: str,
    previous_agent: dict[str, object],
) -> dict[str, object]:
    context_contract = live_agent_context_contract_with_join_semantics(
        agent.get("provider_kind"),
        agent.get("connection_kind"),
        agent.get("join_semantics"),
    )
    details = {
        "agent_id": clean_lobby_text(agent.get("agent_id") or clean_agent_id, limit=64),
        "meeting_id": clean_lobby_text(agent.get("meeting_id"), limit=128),
        "provider_kind": clean_lobby_text(agent.get("provider_kind"), limit=64),
        "connection_kind": clean_lobby_text(agent.get("connection_kind"), limit=64),
        "join_semantics": context_contract["join_semantics"],
        "context_durability": context_contract["context_durability"],
        "sandbox_enforcement": context_contract["sandbox_enforcement"],
        "engagement_mode": clean_lobby_text(agent.get("engagement_mode"), limit=64),
        "previous_status": clean_lobby_text(previous_agent.get("status"), limit=32),
        "registered_status": clean_lobby_text(agent.get("status"), limit=32),
    }
    details.update(_live_agent_register_admission_details(output_root, agent))
    return details


def _strict_meeting_record_for_admission(output_root: Path, meeting_id: str) -> dict[str, object]:
    """Admission evidence requires a host-written meeting record.

    Council meetings carry meeting.json. Live-agent meetings are created by
    the host via start_live_agent_meeting, whose live_state.json includes the
    full meeting shape (question/topic/roles). A skeletal live_state.json that
    only lists agent_bindings is runner-writable state and must never grant
    admission.
    """
    meeting_dir = _safe_meeting_dir(output_root, meeting_id)
    meeting_path = meeting_dir / "meeting.json"
    live_path = meeting_dir / "live_state.json"
    if meeting_path.exists():
        meeting = json.loads(meeting_path.read_text(encoding="utf-8"))
        return _merge_live_progress_from_path(meeting, live_path)
    if live_path.exists():
        record = json.loads(live_path.read_text(encoding="utf-8"))
        if isinstance(record, dict) and all(key in record for key in ("question", "topic", "roles")):
            return record
    raise ValueError("Meeting record is missing.")


def _live_agent_register_admission_details(output_root: Path, agent: dict[str, object]) -> dict[str, object]:
    agent_id = clean_lobby_text(agent.get("agent_id"), limit=64)
    meeting_id = clean_lobby_text(agent.get("meeting_id"), limit=128)
    if not meeting_id:
        return {"admission_status": "lobby_only", "host_approved_binding": False}
    try:
        meeting = _strict_meeting_record_for_admission(output_root, meeting_id)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"admission_status": "meeting_missing", "host_approved_binding": False}
    return _live_agent_admission_details_from_meeting(meeting, agent, agent_id=agent_id)


def _live_agent_roster_admission_details(output_root: Path, agent: dict[str, object]) -> dict[str, object]:
    agent_id = clean_lobby_text(agent.get("agent_id"), limit=64)
    meeting_id = clean_lobby_text(agent.get("meeting_id"), limit=128)
    if not meeting_id:
        return {"admission_status": "lobby_only", "host_approved_binding": False}
    try:
        meeting = _strict_meeting_record_for_admission(output_root, meeting_id)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"admission_status": "meeting_missing", "host_approved_binding": False}
    return _live_agent_admission_details_from_meeting(meeting, agent, agent_id=agent_id)


def _live_agent_admission_details_from_meeting(
    meeting: dict[str, object],
    agent: dict[str, object],
    *,
    agent_id: str,
) -> dict[str, object]:
    binding = _meeting_binding_for_agent(meeting, agent_id)
    if not binding:
        return {"admission_status": "meeting_lobby_only", "host_approved_binding": False}

    provider_id = clean_lobby_text(binding.get("provider_id"), limit=128)
    providers = meeting.get("provider_configs") if isinstance(meeting.get("provider_configs"), dict) else {}
    provider = providers.get(provider_id) if isinstance(providers.get(provider_id), dict) else {}
    binding_provider_kind = clean_lobby_text(provider.get("kind"), limit=64)
    registered_provider_kind = clean_lobby_text(agent.get("provider_kind"), limit=64)
    conflicts: list[str] = []
    if not provider:
        conflicts.append("binding_provider_missing")
    elif binding_provider_kind and registered_provider_kind and binding_provider_kind != registered_provider_kind:
        conflicts.append("provider_kind_mismatch")

    admission_status = "binding_conflict" if conflicts else "bound_to_meeting"
    details: dict[str, object] = {
        "admission_status": admission_status,
        "host_approved_binding": admission_status == "bound_to_meeting",
        "binding_role_id": clean_lobby_text(binding.get("role_id"), limit=128),
        "binding_provider_id": provider_id,
        "binding_provider_kind": binding_provider_kind,
        "binding_permission_profile_id": clean_lobby_text(binding.get("permission_profile_id"), limit=128),
        "binding_join_mode": clean_lobby_text(binding.get("join_mode"), limit=64),
    }
    if conflicts:
        details["binding_conflicts"] = conflicts
    return details


def _meeting_binding_for_agent(meeting: dict[str, object], agent_id: str) -> dict[str, object]:
    for binding in _as_dict_list(meeting.get("agent_bindings")):
        if clean_lobby_text(binding.get("agent_id"), limit=64) == agent_id:
            return binding
    return {}


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


def _live_events_with_projected_return_packets(
    events: list[dict[str, object]],
    *,
    meeting_dir: Path,
    meeting: dict[str, object],
    agent: dict[str, object],
) -> list[dict[str, object]]:
    agent_id = clean_lobby_text(agent.get("agent_id"), limit=64)
    if not agent_id:
        return []
    cursor = clean_lobby_text(agent.get("last_observed_live_event_id"), limit=128)
    visible_events = _visible_live_events_pending_for_agent(_live_events_visible_to_agent(events, agent_id), agent_id, cursor)
    projected_events = _projected_return_packet_events(
        meeting_dir,
        meeting=meeting,
        agent_id=agent_id,
        cursor=cursor,
        visible_events=visible_events,
    )
    existing_artifacts = {
        (
            str(event.get("artifact_path") or ""),
            str(event.get("artifact_json_path") or ""),
        )
        for event in visible_events
        if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
    }
    for event in projected_events:
        artifact_key = (str(event.get("artifact_path") or ""), str(event.get("artifact_json_path") or ""))
        if artifact_key in existing_artifacts:
            continue
        visible_events.append(event)
    return visible_events


def _projected_return_packet_events(
    meeting_dir: Path,
    *,
    meeting: dict[str, object],
    agent_id: str,
    cursor: str,
    visible_events: list[dict[str, object]],
) -> list[dict[str, object]]:
    role_names = {
        role_id: str(role.get("display_name") or role_id)
        for role in _as_dict_list(meeting.get("roles"))
        if (role_id := clean_lobby_text(role.get("id"), limit=128))
    }
    events: list[dict[str, object]] = []
    full_events: list[dict[str, object]] | None = None
    visible_artifacts = {
        (
            str(event.get("artifact_path") or ""),
            str(event.get("artifact_json_path") or ""),
        )
        for event in visible_events
        if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
    }
    for binding in _as_dict_list(meeting.get("agent_bindings")):
        if clean_lobby_text(binding.get("agent_id"), limit=64) != agent_id:
            continue
        role_id = clean_lobby_text(binding.get("role_id"), limit=128)
        if not role_id:
            continue
        packet_path = meeting_dir / "return_packets" / f"{role_id}.md"
        packet_json_path = meeting_dir / "return_packets" / f"{role_id}.json"
        if not packet_path.exists() or not packet_json_path.exists():
            continue
        artifact_path = f"return_packets/{role_id}.md"
        artifact_json_path = f"return_packets/{role_id}.json"
        fallback_event_id = _projected_return_packet_event_id(
            meeting_id=clean_lobby_text(meeting.get("meeting_id"), limit=128) or meeting_dir.name,
            agent_id=agent_id,
            role_id=role_id,
            artifact_path=artifact_path,
        )
        if cursor == fallback_event_id:
            continue
        artifact_key = (artifact_path, artifact_json_path)
        if artifact_key in visible_artifacts:
            continue
        if full_events is None:
            full_events = read_live_events(meeting_dir, limit=None)
        original_event = _return_packet_artifact_event(
            full_events,
            agent_id=agent_id,
            artifact_path=artifact_path,
            artifact_json_path=artifact_json_path,
        )
        meeting_id = clean_lobby_text(meeting.get("meeting_id"), limit=128) or meeting_dir.name
        event_id = clean_lobby_text(original_event.get("id") if original_event else "", limit=128)
        if not event_id:
            event_id = fallback_event_id
        if _return_packet_event_observed(
            cursor,
            event_id=event_id,
            fallback_event_id=fallback_event_id,
            original_event=original_event,
            full_events=full_events,
        ):
            continue
        created_at = (
            clean_lobby_text(original_event.get("created_at"), limit=128)
            if original_event
            else _return_packet_projection_created_at(packet_path, packet_json_path)
        )
        events.append(
            {
                "id": event_id,
                "created_at": created_at,
                "kind": "artifact",
                "meeting_id": meeting_id,
                "channel": "system",
                "audience": f"agent:{agent_id}",
                "official_record": False,
                "actor_id": "",
                "target_agent_id": agent_id,
                "source_event_id": "",
                "role_id": role_id,
                "display_name": role_names.get(role_id, role_id),
                "artifact_kind": "return_packet",
                "artifact_path": artifact_path,
                "artifact_json_path": artifact_json_path,
                "content": f"Return packet ready: {artifact_path}",
                "projected": True,
            }
        )
    return events


def _return_packet_read_candidate(
    meeting_dir: Path,
    *,
    meeting: dict[str, object],
    agent_id: str,
    source_event_id: str,
) -> dict[str, object] | None:
    if not agent_id or not source_event_id:
        return None
    meeting_id = clean_lobby_text(meeting.get("meeting_id"), limit=128) or meeting_dir.name
    full_events = read_live_events(meeting_dir, limit=None)
    for binding in _as_dict_list(meeting.get("agent_bindings")):
        if clean_lobby_text(binding.get("agent_id"), limit=64) != agent_id:
            continue
        role_id = clean_lobby_text(binding.get("role_id"), limit=128)
        paths = _return_packet_role_paths(meeting_dir, role_id)
        if paths is None:
            continue
        packet_path, packet_json_path, artifact_path, artifact_json_path = paths
        if not packet_path.exists() or not packet_json_path.exists():
            continue
        fallback_event_id = _projected_return_packet_event_id(
            meeting_id=meeting_id,
            agent_id=agent_id,
            role_id=role_id,
            artifact_path=artifact_path,
        )
        original_event = _return_packet_artifact_event(
            full_events,
            agent_id=agent_id,
            artifact_path=artifact_path,
            artifact_json_path=artifact_json_path,
        )
        original_event_id = clean_lobby_text(original_event.get("id") if original_event else "", limit=128)
        if source_event_id not in {original_event_id, fallback_event_id}:
            continue
        event = original_event or {
            "id": fallback_event_id,
            "kind": "artifact",
            "meeting_id": meeting_id,
            "channel": "system",
            "audience": f"agent:{agent_id}",
            "official_record": False,
            "target_agent_id": agent_id,
            "role_id": role_id,
            "artifact_kind": "return_packet",
            "artifact_path": artifact_path,
            "artifact_json_path": artifact_json_path,
            "projected": True,
        }
        return {
            "role_id": role_id,
            "artifact_path": artifact_path,
            "artifact_json_path": artifact_json_path,
            "packet_path": packet_path,
            "packet_json_path": packet_json_path,
            "event": event,
        }
    return None


def _return_packet_role_paths(meeting_dir: Path, role_id: str) -> tuple[Path, Path, str, str] | None:
    if not role_id:
        return None
    markdown_name = f"{role_id}.md"
    json_name = f"{role_id}.json"
    if Path(markdown_name).name != markdown_name or Path(json_name).name != json_name:
        return None
    packet_dir = (meeting_dir / "return_packets").resolve()
    packet_path = (packet_dir / markdown_name).resolve()
    packet_json_path = (packet_dir / json_name).resolve()
    if packet_path.parent != packet_dir or packet_json_path.parent != packet_dir:
        return None
    return packet_path, packet_json_path, f"return_packets/{markdown_name}", f"return_packets/{json_name}"


def _visible_live_events_pending_for_agent(
    events: list[dict[str, object]],
    agent_id: str,
    cursor: str,
) -> list[dict[str, object]]:
    if not cursor:
        return events
    pending_events: list[dict[str, object]] = []
    for event in events:
        if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet":
            meeting_id = clean_lobby_text(event.get("meeting_id"), limit=128)
            role_id = clean_lobby_text(event.get("role_id"), limit=128)
            artifact_path = clean_lobby_text(event.get("artifact_path"), limit=256)
            if meeting_id and role_id and artifact_path:
                fallback_event_id = _projected_return_packet_event_id(
                    meeting_id=meeting_id,
                    agent_id=agent_id,
                    role_id=role_id,
                    artifact_path=artifact_path,
                )
                if cursor == fallback_event_id:
                    continue
        pending_events.append(event)
    return pending_events


def _return_packet_artifact_event(
    events: list[dict[str, object]],
    *,
    agent_id: str,
    artifact_path: str,
    artifact_json_path: str,
) -> dict[str, object]:
    matching_event: dict[str, object] = {}
    for event in events:
        if event.get("kind") != "artifact" or event.get("artifact_kind") != "return_packet":
            continue
        if str(event.get("target_agent_id") or "") != agent_id and str(event.get("audience") or "") != f"agent:{agent_id}":
            continue
        if str(event.get("artifact_path") or "") != artifact_path:
            continue
        if str(event.get("artifact_json_path") or "") != artifact_json_path:
            continue
        matching_event = event
    return matching_event


def _return_packet_event_observed(
    cursor: str,
    *,
    event_id: str,
    fallback_event_id: str,
    original_event: dict[str, object],
    full_events: list[dict[str, object]],
) -> bool:
    if not cursor:
        return False
    if cursor in {event_id, fallback_event_id}:
        return True
    cursor_index = _live_event_index(full_events, cursor)
    if not original_event:
        return cursor_index is not None
    event_index = _live_event_index(full_events, event_id)
    return cursor_index is not None and event_index is not None and cursor_index >= event_index


def _live_event_index(events: list[dict[str, object]], event_id: str) -> int | None:
    for index, event in enumerate(events):
        if str(event.get("id") or "") == event_id:
            return index
    return None


def _projected_return_packet_event_id(*, meeting_id: str, agent_id: str, role_id: str, artifact_path: str) -> str:
    return uuid5(NAMESPACE_URL, f"agentsassemble:return-packet:{meeting_id}:{agent_id}:{role_id}:{artifact_path}").hex[:12]


def _return_packet_projection_created_at(packet_path: Path, packet_json_path: Path) -> str:
    try:
        packet_stat = packet_path.stat()
        packet_json_stat = packet_json_path.stat()
    except OSError:
        return ""
    version_ns = max(packet_stat.st_mtime_ns, packet_json_stat.st_mtime_ns)
    return datetime.fromtimestamp(version_ns / 1_000_000_000, UTC).isoformat()


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


def _meeting_live_agent_turn_preset_path(path: str) -> str | None:
    return _meeting_live_agent_turn_action_path(path, "preset")


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


def _discovery_operation_details(discoveries: list[object], approval_filter: object = None) -> dict[str, object]:
    return {
        "join_semantics": _discovery_operation_values(discoveries, "join_semantics"),
        "context_durability": _discovery_operation_values(discoveries, "context_durability"),
        "sandbox_enforcement": _discovery_operation_values(discoveries, "sandbox_enforcement"),
        "evidence_basis": _discovery_operation_values(discoveries, "evidence_basis"),
        "approval_required": sum(
            1
            for item in discoveries
            if isinstance(item, dict) and item.get("available") and item.get("included") and item.get("requires_approval")
        ),
        **_discovery_approval_operation_details(approval_filter),
    }


def _discovery_approval_operation_details(approval_filter: object) -> dict[str, object]:
    if not isinstance(approval_filter, dict):
        return {}
    approved_agents = _safe_payload_strings(approval_filter.get("approved_agents"), limit=64)
    excluded_agents = _safe_payload_strings(approval_filter.get("excluded_agents"), limit=64)
    approved_clis = _safe_payload_strings(approval_filter.get("approved_commands"), limit=64)
    excluded_clis = _safe_payload_strings(approval_filter.get("excluded_commands"), limit=64)
    approved_count = _payload_nonnegative_int(approval_filter.get("approved_count"), 0)
    unmatched_count = _payload_nonnegative_int(approval_filter.get("unmatched_approval_count"), 0)
    if not (approved_agents or excluded_agents or approved_clis or excluded_clis or approved_count or unmatched_count):
        return {}
    details: dict[str, object] = {
        "approved_count": approved_count,
        "excluded_agent_count": len(excluded_agents),
        "unmatched_approval_count": unmatched_count,
    }
    if approved_agents:
        details["approved_agent_ids"] = approved_agents[:10]
    if approved_clis:
        details["approved_cli_count"] = len(approved_clis)
    if excluded_clis:
        details["excluded_cli_count"] = len(excluded_clis)
    return details


def _discovery_operation_values(discoveries: list[object], field_name: str) -> list[str]:
    values = set()
    for item in discoveries:
        if not isinstance(item, dict) or not item.get("available"):
            continue
        value = clean_lobby_text(item.get(field_name), limit=128)
        if value:
            values.add(value)
    return sorted(values)


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
        "finalization_status": _operation_result_status(smoke.get("finalization_status")),
        "finalization_official_event_count": _payload_nonnegative_int(
            smoke.get("finalization_official_event_count"),
            0,
        ),
        "return_packet_event_count": _payload_nonnegative_int(smoke.get("return_packet_event_count"), 0),
        "artifact_status": _operation_result_status(smoke.get("artifact_status")),
        "artifact_paths": _safe_payload_strings(smoke.get("artifact_paths"), limit=128),
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


def _safe_real_session_smoke_result(smoke: dict[str, object]) -> dict[str, object]:
    return {
        "status": _operation_result_status(smoke.get("status")),
        "meeting_id": clean_lobby_text(smoke.get("meeting_id"), limit=128),
        "group_id": clean_lobby_text(smoke.get("group_id"), limit=128),
        "approval_required": smoke.get("approval_required") is True,
        "approved": smoke.get("approved") is True,
        "diagnostic": smoke.get("diagnostic") is True,
        "start_status": _operation_result_status(smoke.get("start_status")),
        "expected_agent_count": _payload_nonnegative_int(smoke.get("expected_agent_count"), 0),
        "connected_agent_count": _payload_nonnegative_int(smoke.get("connected_agent_count"), 0),
        "reply_probe_status": _operation_result_status(smoke.get("reply_probe_status")),
        "reply_probe_count": _payload_nonnegative_int(smoke.get("reply_probe_count"), 0),
        "reply_probe_ok_count": _payload_nonnegative_int(smoke.get("reply_probe_ok_count"), 0),
        "official_round_smoke": smoke.get("official_round_smoke") is True,
        "official_rounds_status": _operation_result_status(smoke.get("official_rounds_status")),
        "official_round_count": _payload_nonnegative_int(smoke.get("official_round_count"), 0),
        "official_answered_round_count": _payload_nonnegative_int(smoke.get("official_answered_round_count"), 0),
        "official_timeout_round_count": _payload_nonnegative_int(smoke.get("official_timeout_round_count"), 0),
        "official_skipped_round_count": _payload_nonnegative_int(smoke.get("official_skipped_round_count"), 0),
        "restart_smoke": smoke.get("restart_smoke") is True,
        "restart_status": _operation_result_status(smoke.get("restart_status")),
        "post_restart_expected_agent_count": _payload_nonnegative_int(smoke.get("post_restart_expected_agent_count"), 0),
        "post_restart_connected_agent_count": _payload_nonnegative_int(smoke.get("post_restart_connected_agent_count"), 0),
        "post_restart_reply_probe_status": _operation_result_status(smoke.get("post_restart_reply_probe_status")),
        "post_restart_reply_probe_count": _payload_nonnegative_int(smoke.get("post_restart_reply_probe_count"), 0),
        "post_restart_reply_probe_ok_count": _payload_nonnegative_int(smoke.get("post_restart_reply_probe_ok_count"), 0),
        "stop_status": _operation_result_status(smoke.get("stop_status")),
        "post_stop_process_status": _operation_result_status(smoke.get("post_stop_process_status")),
    }


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
    long_session_sections = {
        "observations": (
            "observation",
            ("lobby_behind_count", "live_behind_count", "error_count"),
        ),
        "shared_memory": (
            "shared_memory",
            ("ready_sessions", "with_memory"),
        ),
        "session_runs": (
            "session_run",
            ("active", "retrying"),
        ),
        "session_run_monitor": (
            "session_run_monitor",
            ("last_result_count",),
        ),
    }
    for section_name, (detail_name, count_names) in long_session_sections.items():
        section = health.get(section_name)
        if not isinstance(section, dict):
            continue
        attention = _safe_health_operation_strings(section.get("attention"), limit=128)
        if attention:
            details[f"health_{detail_name}_attention"] = attention
        for count_name in count_names:
            count = _payload_nonnegative_int(section.get(count_name), 0)
            if count:
                details[f"health_{detail_name}_{count_name}"] = count
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


def _live_agent_turn_sequence_status(
    answered_count: int,
    timeout_count: int,
    skipped_count: int,
    cancelled_count: int,
    *,
    turn_count: int,
) -> str:
    if skipped_count:
        return "stopped"
    if timeout_count:
        return "timeout"
    if cancelled_count:
        return "cancelled"
    if answered_count == turn_count:
        return "answered"
    return "degraded"


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
        "cancelled_count": _payload_nonnegative_int(sequence.get("cancelled_count"), 0),
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
    details = {
        "finalization_status": _operation_result_status(finalization.get("status")),
        "finalization_reason": clean_lobby_text(finalization.get("reason"), limit=256),
        "finalization_meeting_id": clean_lobby_text(finalization.get("meeting_id") or meeting_id, limit=128),
        "finalization_official_event_count": _payload_nonnegative_int(finalization.get("official_event_count"), 0),
        "finalization_artifact_event_id": clean_lobby_text(finalization.get("artifact_event_id"), limit=128),
    }
    shared_memory = finalization.get("shared_memory") if isinstance(finalization.get("shared_memory"), dict) else {}
    if shared_memory:
        details.update(_shared_memory_operation_details(shared_memory))
    return details


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
        "finalization_status": _operation_result_status(smoke.get("finalization_status")),
        "finalization_official_event_count": _payload_nonnegative_int(
            smoke.get("finalization_official_event_count"),
            0,
        ),
        "return_packet_event_count": _payload_nonnegative_int(smoke.get("return_packet_event_count"), 0),
        "artifact_status": _operation_result_status(smoke.get("artifact_status")),
        "artifact_paths": _safe_payload_strings(smoke.get("artifact_paths"), limit=128),
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


def _real_session_smoke_operation_details(smoke: dict[str, object]) -> dict[str, object]:
    return {
        "group_id": clean_lobby_text(smoke.get("group_id"), limit=128),
        "meeting_id": clean_lobby_text(smoke.get("meeting_id"), limit=128),
        "result_status": _operation_result_status(smoke.get("status")),
        "approval_required": smoke.get("approval_required") is True,
        "approved": smoke.get("approved") is True,
        "diagnostic": smoke.get("diagnostic") is True,
        "start_status": _operation_result_status(smoke.get("start_status")),
        "expected_agent_count": _payload_nonnegative_int(smoke.get("expected_agent_count"), 0),
        "connected_agent_count": _payload_nonnegative_int(smoke.get("connected_agent_count"), 0),
        "reply_probe_status": _operation_result_status(smoke.get("reply_probe_status")),
        "reply_probe_count": _payload_nonnegative_int(smoke.get("reply_probe_count"), 0),
        "reply_probe_ok_count": _payload_nonnegative_int(smoke.get("reply_probe_ok_count"), 0),
        "official_round_smoke": smoke.get("official_round_smoke") is True,
        "official_rounds_status": _operation_result_status(smoke.get("official_rounds_status")),
        "official_round_count": _payload_nonnegative_int(smoke.get("official_round_count"), 0),
        "official_answered_round_count": _payload_nonnegative_int(smoke.get("official_answered_round_count"), 0),
        "official_timeout_round_count": _payload_nonnegative_int(smoke.get("official_timeout_round_count"), 0),
        "official_skipped_round_count": _payload_nonnegative_int(smoke.get("official_skipped_round_count"), 0),
        "restart_smoke": smoke.get("restart_smoke") is True,
        "restart_status": _operation_result_status(smoke.get("restart_status")),
        "post_restart_expected_agent_count": _payload_nonnegative_int(smoke.get("post_restart_expected_agent_count"), 0),
        "post_restart_connected_agent_count": _payload_nonnegative_int(smoke.get("post_restart_connected_agent_count"), 0),
        "post_restart_reply_probe_status": _operation_result_status(smoke.get("post_restart_reply_probe_status")),
        "post_restart_reply_probe_count": _payload_nonnegative_int(smoke.get("post_restart_reply_probe_count"), 0),
        "post_restart_reply_probe_ok_count": _payload_nonnegative_int(smoke.get("post_restart_reply_probe_ok_count"), 0),
        "stop_status": _operation_result_status(smoke.get("stop_status")),
        "post_stop_process_status": _operation_result_status(smoke.get("post_stop_process_status")),
    }


def _real_session_smoke_error_details(payload: dict[str, object]) -> dict[str, object]:
    return {
        "group_id": clean_lobby_text(payload.get("group_id"), limit=128),
        "meeting_id": clean_lobby_text(payload.get("meeting_id"), limit=128),
    }


def _real_session_smoke_has_explicit_configs(payload: dict[str, object]) -> bool:
    return all(
        str(value or "").strip()
        for value in (
            payload.get("live_agent_config_path") or payload.get("live_agent_config"),
            payload.get("council_config_path") or payload.get("council_config"),
            payload.get("agent_config_path") or payload.get("agent_config"),
        )
    )


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
    ensure_reason = _safe_session_ensure_reason(session.get("ensure_reason"))
    if ensure_reason:
        details["ensure_reason"] = ensure_reason
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


def _safe_session_ensure_reason(value: object) -> str:
    reason = clean_lobby_text(value, limit=64)
    return reason if reason in SESSION_ENSURE_REASONS else ""


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
    details = {
        "result_status": _operation_result_status(result.get("status")),
        "meeting_id": clean_lobby_text(result.get("meeting_id") or meeting_id, limit=128),
        "official_event_count": _payload_nonnegative_int(result.get("official_event_count"), 0),
        "artifact_event_id": clean_lobby_text(result.get("artifact_event_id"), limit=128),
        "cancelled_pending_count": _payload_nonnegative_int(result.get("cancelled_pending_count"), 0),
        "cancelled_event_ids": _safe_payload_strings(result.get("cancelled_event_ids"), limit=128),
        "cancelled_turn_request_ids": _safe_payload_strings(result.get("cancelled_turn_request_ids"), limit=128),
    }
    shared_memory = result.get("shared_memory") if isinstance(result.get("shared_memory"), dict) else {}
    if shared_memory:
        details.update(_shared_memory_operation_details(shared_memory))
    return details


def _shared_memory_operation_details(memory: dict[str, object]) -> dict[str, object]:
    return {
        "shared_memory_official_event_count": _payload_nonnegative_int(memory.get("official_event_count"), 0),
        "shared_memory_last_event_id": clean_lobby_text(memory.get("last_official_event_id"), limit=128),
        "shared_memory_decision_count": _payload_nonnegative_int(memory.get("decision_count"), _memory_item_count(memory.get("decisions"))),
        "shared_memory_open_question_count": _payload_nonnegative_int(
            memory.get("open_question_count"),
            _memory_item_count(memory.get("open_questions")),
        ),
        "shared_memory_action_item_count": _payload_nonnegative_int(
            memory.get("action_item_count"),
            _memory_item_count(memory.get("action_items")),
        ),
    }


def _memory_item_count(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


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


def _session_run_action_target_details(payload: dict[str, object]) -> dict[str, str]:
    return {
        "meeting_id": clean_lobby_text(payload.get("meeting_id"), limit=128),
        "group_id": clean_lobby_text(payload.get("group_id"), limit=128),
    }


def _latest_session_run_for_action_target(
    session_run_controller: LiveAgentSessionRunController,
    payload: dict[str, object],
) -> dict[str, object]:
    details = _session_run_action_target_details(payload)
    if not details["meeting_id"] or not details["group_id"]:
        raise ValueError("Missing session run id")
    runs = session_run_controller.list_runs(
        limit=1,
        meeting_id=details["meeting_id"],
        group_id=details["group_id"],
    )
    if not runs:
        raise ValueError("No matching live-agent session run for meeting group target.")
    return runs[-1]


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


def _turn_preset_request_operation_details(payload: dict[str, object], meeting_id: str) -> dict[str, object]:
    return {
        "meeting_id": meeting_id,
        "preset_id": clean_lobby_text(payload.get("preset_id") or payload.get("preset"), limit=128),
        "role_ids": _safe_payload_role_ids(payload.get("role_ids")),
        "timeout_seconds": _payload_nonnegative_float(payload.get("timeout_seconds", payload.get("timeout")), 30.0),
        "stop_on_timeout": _payload_bool(payload.get("stop_on_timeout")),
    }


def _turn_preset_operation_details(preset_result: dict[str, object], meeting_id: str) -> dict[str, object]:
    details = _turn_sequence_operation_details(preset_result, meeting_id)
    details["preset_id"] = clean_lobby_text(preset_result.get("preset_id"), limit=128)
    details["round_id"] = clean_lobby_text(preset_result.get("round_id"), limit=128)
    details["role_ids"] = _safe_payload_role_ids(preset_result.get("role_ids"))
    return details


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


def _print_gui_startup_banner(server_url: str, *, frontend_dist_root: Path | None = None) -> None:
    base_url = server_url.rstrip("/")
    dist_status = frontend_dist_status(frontend_dist_root)
    print(f"AgentsAssemble GUI: {base_url}")
    if dist_status.static_available:
        print(f"- Operator console (default): {base_url}/ (React)")
        print(f"- Same Discord room client alias: {base_url}/app/")
    else:
        print(f"- Operator console unavailable until the React build exists: {base_url}/")
        print(f"- Build React for the default console: {REACT_APP_BUILD_COMMAND}")
        print(f"- Same Discord room client alias: {base_url}/app/ (build required)")


_LOOPBACK_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}
_PUBLIC_INVITE_CORS_METHODS = "GET, POST, OPTIONS"
_PUBLIC_INVITE_CORS_HEADERS = "Authorization, Content-Type, Last-Event-ID"


def _is_loopback_host(host: object) -> bool:
    return str(host or "").strip().strip("[]").lower() in _LOOPBACK_HOSTNAMES


def _split_authority_host_port(authority: str) -> tuple[str, str]:
    authority = authority.strip()
    if authority.startswith("["):
        host, _, rest = authority[1:].partition("]")
        return host.strip().lower(), (rest[1:].strip() if rest.startswith(":") else "")
    if authority.count(":") == 1:
        host, _, port = authority.partition(":")
        return host.strip().lower(), port.strip()
    return authority.lower(), ""


def _host_header_is_trusted(host_header: object) -> bool:
    hostname, _ = _split_authority_host_port(str(host_header or ""))
    if hostname in _LOOPBACK_HOSTNAMES:
        return True
    public_url = get_public_url()
    if not public_url:
        return False
    return hostname == (urlparse(public_url).hostname or "").lower()


def _origin_is_trusted(origin: str) -> bool:
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"}:
        return False
    hostname = (parsed.hostname or "").lower()
    return hostname in _LOOPBACK_HOSTNAMES


def _origin_matches_public_url(origin: str) -> bool:
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"}:
        return False
    hostname = (parsed.hostname or "").lower()
    public_url = get_public_url()
    return bool(public_url) and hostname == (urlparse(public_url).hostname or "").lower()


def _origin_is_loopback_or_empty(origin: object) -> bool:
    origin_text = str(origin or "").strip()
    return not origin_text or _origin_is_trusted(origin_text)


def _public_invite_route_allowed(path: str, method: str) -> bool:
    method = method.upper()
    if method == "GET":
        return (
            path
            in {
                "/join",
                "/join/",
                "/api",
                "/api/",
                "/api/room/events",
                "/api/room/lobby",
                "/api/room/vote",
                "/api/live-agent-flow",
                # Roster for the member panel; the handler requires a session
                # (or host/operator) when the request comes from outside.
                "/api/room-members",
                # Moderator-gated listings so the operator's invite tools work
                # away from the desk.
                "/api/room-invite/sessions",
                "/api/room-invite/invites",
            }
            or path.startswith("/app/assets/")
        )
    if method == "POST":
        return path in {
            "/api/room-invite/join",
            "/api/room-invite/leave",
            "/api/room-invite/companion",
            "/api/room/say",
            # Host-token gated: lets the operator claim a new device (e.g. a
            # phone) through the public entrance.
            "/api/host/claim",
            # Moderator-gated (host token OR operator session).
            "/api/room-members/mute",
            "/api/room-members/kick",
            "/api/room-invite/create",
            "/api/room-invite/revoke",
        }
    if method == "OPTIONS":
        return _public_invite_route_allowed(path, "GET") or _public_invite_route_allowed(path, "POST")
    return False


def _request_trusted(
    bound_host: object,
    host_header: object,
    origin: object,
    *,
    path: str = "",
    method: str = "GET",
) -> bool:
    # A non-loopback bind is an explicit operator choice to expose the
    # unauthenticated control plane (see the startup warning), so the loopback
    # allowlist is only enforced for the default loopback bind, where it blocks
    # DNS-rebinding/CSRF driven by a browser.
    if not _is_loopback_host(bound_host):
        return True
    host_trusted = _host_header_is_trusted(host_header)
    host_name, _ = _split_authority_host_port(str(host_header or ""))
    host_is_loopback = host_name in _LOOPBACK_HOSTNAMES
    host_is_public = host_trusted and not host_is_loopback
    if not host_trusted:
        return False
    if host_is_public and not _public_invite_route_allowed(path, method):
        return False
    origin_text = str(origin or "").strip()
    if not origin_text:
        return True
    if host_is_loopback:
        return _origin_is_trusted(origin_text)
    if origin_text == "null":
        return True
    return _origin_matches_public_url(origin_text)


def _make_handler(
    output_root: Path,
    *,
    process_supervisor: LiveAgentProcessSupervisor | None = None,
    session_run_controller: LiveAgentSessionRunController | None = None,
    session_run_monitor: LiveAgentSessionRunMonitor | None = None,
    flow_supervisor: LiveAgentFlowSupervisor | None = None,
    frontend_dist_root: Path | None = None,
    public_tunnel_manager: PublicTunnelManager | None = None,
    live_agent_login_launcher: object | None = None,
    live_agent_login_command_resolver: object | None = None,
) -> type[BaseHTTPRequestHandler]:
    configure_room_invite_store(default_room_invite_store_path(output_root))
    # Identity (users/credentials/memberships) lives in one SQLite file; a
    # legacy users.json from the JSON era is imported on first run.
    configure_room_users_store(default_identity_db_path(output_root))
    react_app_root = (frontend_dist_root or default_frontend_dist_root()).resolve()
    live_agent_process_supervisor = process_supervisor or LiveAgentProcessSupervisor(output_root)
    live_agent_session_run_controller = session_run_controller or LiveAgentSessionRunController(output_root)
    live_agent_flow_supervisor = flow_supervisor or LiveAgentFlowSupervisor(output_root)
    invite_tunnel_manager = public_tunnel_manager or PublicTunnelManager()
    # WS 전환 (WS-4): single-use tickets bind a verified session to a /ws open
    # (browsers can't set Authorization on `new WebSocket`).
    ws_ticket_store = WsTicketStore()

    def _ws_room_deps() -> WsRoomDeps:
        # Reuse the proven SSE snapshot machinery + the governed say append path,
        # so the WS transport behaves exactly like the HTTP/SSE one (no pub/sub yet).
        def read_lobby_after(meeting_id: str, after_id: str) -> tuple[list, str]:
            payload = _stream_snapshot_payload(
                output_root, "lobby", meeting_id=meeting_id, last_event_id=after_id or None
            )
            events = list(payload.get("events", []))
            return events, (_last_payload_event_id(payload) or after_id)

        def read_roster(meeting_id: str) -> tuple[list, str]:
            payload = _stream_snapshot_payload(output_root, "roster", meeting_id=meeting_id, last_event_id=None)
            return list(payload.get("members", [])), str(_payload_signature(payload) or "")

        def post_say(identity: dict, payload: dict) -> dict:
            # mirrors POST /api/room/say identity injection (never trust client)
            event_payload = dict(payload)
            event_payload["name"] = identity.get("display_name")
            event_payload["actor_id"] = identity.get("agent_id")
            event_payload["actor_type"] = (
                "human" if str(identity.get("participant_type") or "human") == "human" else "agent"
            )
            event_payload["side"] = "other"
            requested_kind = str(event_payload.get("kind") or "")
            event_payload["kind"] = requested_kind if requested_kind in {"vote", "vote_cast"} else "message"
            if identity.get("meeting_id"):
                event_payload["flow_meeting_id"] = identity["meeting_id"]
            return append_lobby_event(
                output_root,
                event_payload,
                allow_flow_metadata=_public_lobby_allows_room_scope(event_payload),
            )

        def set_thinking(identity: dict, on: bool) -> None:
            # Ephemeral "generating a reply" flag → the roster carries a `thinking`
            # bool that the roster push delivers, lighting up the typing indicator.
            mark_thinking(
                str(identity.get("meeting_id") or ""),
                str(identity.get("agent_id") or ""),
                on,
            )

        return WsRoomDeps(
            read_lobby_after=read_lobby_after,
            read_roster=read_roster,
            post_say=post_say,
            is_muted=lambda meeting_id, agent_id: is_room_member_muted(output_root, meeting_id, agent_id),
            set_thinking=set_thinking,
        )
    # R2: route-table dispatcher. Migrated domains register here; do_GET/do_POST
    # try the table first and fall back to the legacy if-chains below.
    route_deps = GuiDeps(
        output_root=output_root,
        process_supervisor=live_agent_process_supervisor,
        read_lobby=read_lobby,
        read_lobby_before=read_lobby_before,
        append_lobby_event=append_lobby_event,
        lobby_payload_with_attachments=lobby_payload_with_attachments,
        public_lobby_allows_room_scope=_public_lobby_allows_room_scope,
        history_page_limit=_history_page_limit,
    )
    route_table = Router()
    register_room_routes(route_table)

    class AgentsAssembleHandler(BaseHTTPRequestHandler):
        def _request_is_trusted(self, *, path: str, method: str) -> bool:
            return _request_trusted(
                self.server.server_address[0],
                self.headers.get("Host"),
                self.headers.get("Origin"),
                path=path,
                method=method,
            )

        def _public_invite_cors_origin(self, *, requested_method: str = "") -> str:
            origin = str(self.headers.get("Origin") or "").strip()
            if not origin:
                return ""
            host_name, _ = _split_authority_host_port(str(self.headers.get("Host") or ""))
            if host_name in _LOOPBACK_HOSTNAMES or not _host_header_is_trusted(self.headers.get("Host")):
                return ""
            path = urlparse(self.path).path
            method = (requested_method or self.command or "").upper()
            if method == "OPTIONS":
                method = str(self.headers.get("Access-Control-Request-Method") or "").upper()
            if not method or not _public_invite_route_allowed(path, method):
                return ""
            if origin == "null":
                return "null"
            if _origin_matches_public_url(origin):
                return origin
            return ""

        def _send_public_invite_cors_headers(self, *, origin: str = "") -> None:
            allow_origin = origin or self._public_invite_cors_origin()
            if not allow_origin:
                return
            self.send_header("Access-Control-Allow-Origin", allow_origin)
            self.send_header("Access-Control-Allow-Methods", _PUBLIC_INVITE_CORS_METHODS)
            self.send_header("Access-Control-Allow-Headers", _PUBLIC_INVITE_CORS_HEADERS)
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if not self._request_is_trusted(path=path, method="GET"):
                self._send_error(HTTPStatus.FORBIDDEN, "Untrusted request host or origin")
                return
            if path == "/ws":
                self._handle_ws_upgrade(query)
                return
            if route_table.dispatch("GET", RequestContext(self, route_deps, parsed, query)):
                return
            if path == "/":
                self._send_react_app_index(react_app_root)
                return
            if path in {"/legacy", "/legacy/"}:
                self._send_error(HTTPStatus.NOT_FOUND, "Legacy console is retired. Use the Discord-style React room client at /.")
                return
            if path in {"/app", "/app/"}:
                self._send_react_app_index(react_app_root)
                return
            if path in {"/join", "/join/"}:
                # AI clients negotiate JSON to get the pre-join manual instead
                # of reverse-engineering the SPA bundle (friend feedback #1).
                accepts_json = "application/json" in str(self.headers.get("Accept") or "")
                wants_json = accepts_json or str(query.get("format", [""])[0]).lower() == "json"
                if wants_json:
                    self._send_json(_pre_join_guide_payload(self._request_server_url()))
                    return
                self._send_react_app_index(react_app_root)
                return
            if path in {"/api", "/api/"}:
                self._send_json(_api_catalog_payload(self._request_server_url()))
                return
            if path.startswith("/app/"):
                rel = unquote(path.removeprefix("/app/"))
                app_path = _safe_static_path(react_app_root, rel)
                if app_path is None:
                    self._send_error(HTTPStatus.NOT_FOUND, "File not found")
                    return
                if app_path.name == "index.html":
                    self._send_react_app_index(react_app_root)
                    return
                self._send_file(
                    app_path,
                    _react_app_content_type(app_path),
                    cache_control=_react_app_cache_control(app_path),
                )
                return
            if path.startswith("/legacy/static/"):
                self._send_error(HTTPStatus.NOT_FOUND, "Legacy static assets are retired.")
                return
            if path.startswith("/static/"):
                self._send_error(HTTPStatus.NOT_FOUND, "Legacy static assets are retired.")
                return
            if path.startswith("/api/attachments/"):
                attachment_id = unquote(path.removeprefix("/api/attachments/"))
                try:
                    metadata, file_path = read_attachment_file(output_root, attachment_id)
                except AttachmentError as error:
                    self._send_error(HTTPStatus.NOT_FOUND, str(error))
                    return
                inline = metadata.get("is_image") is True and "view" in query and "download" not in query
                self._send_attachment_file(file_path, metadata, inline=inline)
                return
            if path == "/api/meetings":
                self._send_json({"meetings": list_meetings(output_root)})
                return
            if path == "/api/events/lobby":
                self._send_sse_stream(
                    "lobby",
                    "lobby",
                    meeting_id=str(query.get("meeting_id", [""])[0] or ""),
                    last_event_id=self._last_event_id(query),
                )
                return
            if path == "/api/public-invite/status":
                self._send_json(self._public_invite_status())
                return
            if path == "/api/side-chat":
                self._send_json(
                    {"events": read_side_chat(output_root, meeting_id=str(query.get("meeting_id", [""])[0] or ""))}
                )
                return
            if path == "/api/events/side-chat":
                self._send_sse_stream(
                    "side_chat",
                    "side_chat",
                    meeting_id=str(query.get("meeting_id", [""])[0] or ""),
                    last_event_id=self._last_event_id(query),
                )
                return
            if path == "/api/providers":
                self._send_json(provider_catalog_payload())
                return
            if path == "/api/model-catalog":
                self._send_json(model_catalog_payload())
                return
            if path == "/api/room-settings":
                self._send_json(room_settings_payload(output_root, room_id=str(query.get("room_id", [""])[0] or "")))
                return
            if path == "/api/room-friends/dm":
                try:
                    self._send_json(
                        room_friend_dm_payload(
                            output_root,
                            str(query.get("friend_id", [""])[0] or ""),
                        )
                    )
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                return
            if path == "/api/room-friends":
                self._send_json(room_friends_payload(output_root, read_live_agents(output_root)))
                return
            if path == "/api/user-profile":
                self._send_json(read_user_profile(output_root))
                return
            if path == "/api/live-agents":
                self._send_json(
                    live_agents_payload(
                        output_root,
                        meeting_id=str(query.get("meeting_id", [""])[0] or ""),
                        agent_ids=query.get("agent_id", []),
                        statuses=query.get("status", []),
                        safe=_payload_bool(query.get("safe", [""])[0]),
                    )
                )
                return
            if path == "/api/live-agent-flow":
                session_token = self._extract_session_token()
                session = verify_session_token(session_token) if session_token else None
                if session_token and not session:
                    self._send_error(HTTPStatus.UNAUTHORIZED, "invalid or expired session")
                    return
                if not session and not self._request_uses_loopback_host():
                    self._send_error(HTTPStatus.UNAUTHORIZED, "session token required")
                    return
                flow_meeting_id = (
                    str(session.get("meeting_id") or "")
                    if session
                    else str(query.get("meeting_id", [""])[0] or "")
                )
                quota_viewer = quota_viewer_for_session(session) if session else quota_viewer_for_host()
                self._send_json(
                    live_agent_flow_supervisor.status(
                        meeting_id=flow_meeting_id,
                        quota_viewer=quota_viewer,
                    )
                )
                return
            if path == "/api/play/mafia":
                try:
                    game = mafia_game_payload(
                        output_root,
                        str(query.get("game_id", [""])[0] or ""),
                        viewer_agent_id=str(query.get("viewer_agent_id", [""])[0] or ""),
                    )
                except ValueError as error:
                    self._send_error(HTTPStatus.NOT_FOUND, str(error))
                    return
                self._send_json({"game": game})
                return
            if path == "/api/live-agent-health":
                self._send_json(
                    live_agent_health_payload(
                        output_root,
                        live_agent_process_supervisor,
                        session_run_monitor=session_run_monitor,
                    )
                )
                return
            if path == "/api/local-resources":
                self._send_json(local_resource_snapshot_payload(live_agent_process_supervisor))
                return
            if path == "/api/release-health":
                self._send_json(release_health_catalog_payload())
                return
            if path == "/api/release-health/queue":
                self._send_json(release_health_queue_payload(output_root=output_root))
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
            if path == "/api/live-agent-create/options":
                self._send_json(frontend_live_agent_options_payload(default_workspace=Path.cwd()))
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
                        run_id=str(query.get("run_id", [""])[0] or ""),
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
            live_agent_return_packet_id = _live_agent_action_path(path, "return-packet")
            if live_agent_return_packet_id is not None:
                try:
                    self._send_json(
                        live_agent_return_packet_payload(
                            output_root,
                            live_agent_return_packet_id,
                            meeting_id=str(query.get("meeting_id", [""])[0] or ""),
                            source_event_id=str(query.get("source_event_id", [""])[0] or ""),
                        )
                    )
                except ValueError:
                    self._send_error(HTTPStatus.NOT_FOUND, "Return packet not found")
                return
            if path == "/api/codex-sessions":
                self._send_json(codex_sessions_payload(limit=self._limit(query, default=20)))
                return
            if path == "/api/meetings/latest":
                meetings = list_meetings(output_root)
                if not meetings:
                    self._send_json({"meeting": None})
                    return
                self._send_json(build_meeting_payload(Path(str(meetings[0]["path"])), output_root=output_root))
                return
            if path.startswith("/api/meetings/") and path.endswith("/lifecycle"):
                meeting_id = unquote(path.removeprefix("/api/meetings/").removesuffix("/lifecycle").strip("/"))
                try:
                    meeting_dir = _safe_meeting_dir(output_root, meeting_id)
                except ValueError as error:
                    self._send_error(HTTPStatus.NOT_FOUND, str(error))
                    return
                if not meeting_dir.exists():
                    self._send_error(HTTPStatus.NOT_FOUND, "Meeting not found")
                    return
                try:
                    lifecycle_meeting = _read_meeting_record(meeting_dir)
                except (OSError, json.JSONDecodeError):
                    lifecycle_meeting = {"meeting_id": meeting_id}
                self._send_json(
                    {
                        "meeting_id": meeting_id,
                        "lifecycle": project_meeting_lifecycle(
                            meeting_dir,
                            now=time.time(),
                            live_agents=_lifecycle_live_agents_for_meeting(
                                output_root,
                                lifecycle_meeting,
                            ),
                        ),
                    }
                )
                return
            if path.startswith("/api/meetings/") and path.endswith("/workroom-queue"):
                meeting_id = unquote(path.removeprefix("/api/meetings/").removesuffix("/workroom-queue").strip("/"))
                try:
                    meeting_dir = _safe_meeting_dir(output_root, meeting_id)
                except ValueError as error:
                    self._send_error(HTTPStatus.NOT_FOUND, str(error))
                    return
                if not meeting_dir.exists():
                    self._send_error(HTTPStatus.NOT_FOUND, "Meeting not found")
                    return
                self._send_json(
                    build_workroom_queue_payload(
                        meeting_dir,
                        now=time.time(),
                        output_root=output_root,
                    )
                )
                return
            meeting_events_id = self._meeting_events_id(path)
            if meeting_events_id:
                try:
                    meeting_dir = _safe_meeting_dir(output_root, meeting_events_id)
                except ValueError as error:
                    self._send_error(HTTPStatus.NOT_FOUND, str(error))
                    return
                if not meeting_dir.exists():
                    self._send_error(HTTPStatus.NOT_FOUND, "Meeting not found")
                    return
                self._send_sse_stream("meeting", "meeting", meeting_id=meeting_events_id, last_event_id=self._last_event_id(query))
                return
            if path.startswith("/api/meetings/"):
                meeting_id = unquote(path.removeprefix("/api/meetings/"))
                try:
                    meeting_dir = _safe_meeting_dir(output_root, meeting_id)
                except ValueError as error:
                    self._send_error(HTTPStatus.NOT_FOUND, str(error))
                    return
                if not meeting_dir.exists():
                    self._send_error(HTTPStatus.NOT_FOUND, "Meeting not found")
                    return
                self._send_json(build_meeting_payload(meeting_dir, output_root=output_root))
                return
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_OPTIONS(self) -> None:
            parsed = urlparse(self.path)
            requested_method = str(self.headers.get("Access-Control-Request-Method") or "").upper()
            if requested_method and not _public_invite_route_allowed(parsed.path, requested_method):
                self._send_error(HTTPStatus.FORBIDDEN, "Untrusted request host or origin")
                return
            if not self._request_is_trusted(path=parsed.path, method="OPTIONS"):
                self._send_error(HTTPStatus.FORBIDDEN, "Untrusted request host or origin")
                return
            allow_origin = self._public_invite_cors_origin(requested_method=requested_method)
            if not allow_origin:
                self._send_error(HTTPStatus.FORBIDDEN, "Untrusted request host or origin")
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            self._send_public_invite_cors_headers(origin=allow_origin)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if not self._request_is_trusted(path=parsed.path, method="POST"):
                self._send_error(HTTPStatus.FORBIDDEN, "Untrusted request host or origin")
                return
            if route_table.dispatch("POST", RequestContext(self, route_deps, parsed, parse_qs(parsed.query))):
                return
            if parsed.path == "/api/ws-ticket":
                ctx = RequestContext(self, route_deps, parsed, parse_qs(parsed.query))
                session = ctx.require_session()
                if session is None:
                    return  # require_session already sent 401
                ticket = ws_ticket_store.issue(session)
                self._send_json({"ticket": ticket, "ttl_seconds": WS_TICKET_TTL_SECONDS})
                return
            if parsed.path == "/api/demo":
                result = run_demo_meeting(adapter_name="mock", output_root=output_root)
                self._send_json({"meeting_id": result.meeting_id, "path": str(result.meeting_dir)})
                return
            if parsed.path == "/api/attachments":
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
                    attachment = store_uploaded_attachment(output_root, payload)
                except AttachmentError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json({"attachment": attachment})
                return
            if parsed.path == "/api/lobby/promote":
                payload = self._operation_json_payload(operation=LOBBY_PROMOTION_OPERATION)
                if payload is None:
                    return
                raw_event_ids = payload.get("lobby_event_ids") or payload.get("lobby_event_id") or []
                event_ids = raw_event_ids if isinstance(raw_event_ids, list) else [raw_event_ids]
                meeting_id = clean_lobby_text(payload.get("meeting_id"), limit=128)
                try:
                    result = promote_lobby_events_to_official(
                        output_root,
                        meeting_id,
                        event_ids,
                        reason=clean_lobby_text(payload.get("reason"), limit=240),
                    )
                except ValueError as error:
                    record_live_agent_operation(
                        output_root,
                        operation=LOBBY_PROMOTION_OPERATION,
                        status="failed",
                        target_id=meeting_id,
                        error=str(error),
                        details={"source_event_count": len(event_ids)},
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json(result)
                return
            if parsed.path == "/api/lobby":
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
                    payload = lobby_payload_with_attachments(output_root, payload)
                except AttachmentError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                event = append_lobby_event(
                    output_root,
                    payload,
                    allow_flow_metadata=_public_lobby_allows_room_scope(payload),
                )
                self._send_json(
                    {
                        "event": event,
                        "events": read_lobby(
                            output_root,
                            meeting_id=clean_lobby_text(event.get("flow_meeting_id"), limit=128),
                        ),
                    }
                )
                return
            if parsed.path == "/api/side-chat":
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except json.JSONDecodeError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                event = append_side_chat_event(output_root, payload if isinstance(payload, dict) else {})
                self._send_json(
                    {
                        "event": event,
                        "events": read_side_chat(
                            output_root,
                            meeting_id=_side_chat_scope_id(event.get("flow_meeting_id")),
                        ),
                    }
                )
                return
            if parsed.path == "/api/room-settings":
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
                    self._send_json(update_room_settings(output_root, payload))
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                return
            if parsed.path == "/api/room-friends/dm":
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
                    self._send_json(
                        room_friend_direct_dm_payload(
                            output_root,
                            live_agent_process_supervisor,
                            payload,
                            default_server=self._request_server_url(),
                        )
                    )
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                return
            if parsed.path == "/api/room-friends":
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except json.JSONDecodeError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                if not isinstance(payload, dict):
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                friend = upsert_room_friend(output_root, payload)
                self._send_json(
                    {
                        "friend": friend,
                        **room_friends_payload(output_root, read_live_agents(output_root)),
                    }
                )
                return
            if parsed.path == "/api/user-profile":
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                except json.JSONDecodeError:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                if not isinstance(payload, dict):
                    self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                    return
                self._send_json(update_user_profile(output_root, payload))
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
            if parsed.path == "/api/play/mafia/start":
                payload = self._operation_json_payload(operation="mafia.start", target_id="")
                if payload is None:
                    return
                try:
                    game = start_mafia_game(output_root, payload)
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json({"game": game})
                return
            if parsed.path == "/api/play/mafia/chat":
                payload = self._operation_json_payload(operation="mafia.chat", target_id="")
                if payload is None:
                    return
                try:
                    event = post_mafia_chat(output_root, payload)
                    game = mafia_game_payload(
                        output_root,
                        str(payload.get("game_id") or ""),
                        viewer_agent_id=str(payload.get("viewer_agent_id") or payload.get("speaker_id") or ""),
                    )
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json({"event": event, "game": game})
                return
            if parsed.path == "/api/play/mafia/vote":
                payload = self._operation_json_payload(operation="mafia.vote", target_id="")
                if payload is None:
                    return
                try:
                    event = cast_mafia_vote(output_root, payload)
                    game = mafia_game_payload(
                        output_root,
                        str(payload.get("game_id") or ""),
                        viewer_agent_id=str(payload.get("viewer_agent_id") or payload.get("voter_id") or ""),
                    )
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json({"event": event, "game": game})
                return
            if parsed.path == "/api/play/mafia/action":
                payload = self._operation_json_payload(operation="mafia.action", target_id="")
                if payload is None:
                    return
                try:
                    event = submit_mafia_action(output_root, payload)
                    game = mafia_game_payload(
                        output_root,
                        str(payload.get("game_id") or ""),
                        viewer_agent_id=str(payload.get("viewer_agent_id") or payload.get("actor_id") or ""),
                    )
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json({"event": event, "game": game})
                return
            if parsed.path == "/api/play/mafia/resolve":
                payload = self._operation_json_payload(operation="mafia.resolve", target_id="")
                if payload is None:
                    return
                try:
                    resolved = resolve_mafia_phase(output_root, payload)
                    game = mafia_game_payload(
                        output_root,
                        str(resolved.get("game_id") or payload.get("game_id") or ""),
                        viewer_agent_id=str(payload.get("viewer_agent_id") or ""),
                    )
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json({"game": game})
                return
            if parsed.path == "/api/live-agent-flow/start":
                payload = self._operation_json_payload(operation="flow.start", target_id="")
                if payload is None:
                    return
                try:
                    result = live_agent_flow_supervisor.start(payload)
                except ValueError as error:
                    record_live_agent_operation(
                        output_root,
                        operation="flow.start",
                        status="failed",
                        target_id=clean_lobby_text(payload.get("meeting_id"), limit=128),
                        error=str(error),
                        details={
                            "meeting_id": clean_lobby_text(payload.get("meeting_id"), limit=128),
                            "topic": clean_lobby_text(payload.get("topic"), limit=ROOM_TOPIC_LIMIT),
                        },
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                flow = result.get("flow") if isinstance(result.get("flow"), dict) else {}
                record_live_agent_operation(
                    output_root,
                    operation="flow.start",
                    status="success",
                    target_id=clean_lobby_text(flow.get("meeting_id"), limit=128),
                    summary="started Play Mode flow",
                    details={
                        "meeting_id": clean_lobby_text(flow.get("meeting_id"), limit=128),
                        "flow_id": clean_lobby_text(flow.get("flow_id"), limit=128),
                        "agent_count": _payload_nonnegative_int(flow.get("agent_count"), 0),
                        "duration_seconds": _payload_nonnegative_float(flow.get("duration_seconds"), 0.0),
                    },
                )
                self._send_json(result)
                return
            if parsed.path == "/api/live-agent-flow/stop":
                payload = self._operation_json_payload(operation="flow.stop", target_id="")
                if payload is None:
                    return
                result = live_agent_flow_supervisor.stop(payload)
                flow = result.get("flow") if isinstance(result.get("flow"), dict) else {}
                record_live_agent_operation(
                    output_root,
                    operation="flow.stop",
                    status="success",
                    target_id=clean_lobby_text(flow.get("meeting_id"), limit=128),
                    summary="stopped Play Mode flow",
                    details={
                        "meeting_id": clean_lobby_text(flow.get("meeting_id"), limit=128),
                        "flow_id": clean_lobby_text(flow.get("flow_id"), limit=128),
                        "flow_status": clean_lobby_text(flow.get("status"), limit=64),
                    },
                )
                self._send_json(result)
                return
            session_run_pause_id = _live_agent_session_run_action_path(parsed.path, "pause")
            if session_run_pause_id is not None or parsed.path == "/api/live-agent-session-runs/pause":
                self._handle_session_run_action("pause", session_run_pause_id)
                return
            session_run_resume_id = _live_agent_session_run_action_path(parsed.path, "resume")
            if session_run_resume_id is not None or parsed.path == "/api/live-agent-session-runs/resume":
                self._handle_session_run_action("resume", session_run_resume_id)
                return
            session_run_stop_id = _live_agent_session_run_action_path(parsed.path, "stop")
            if session_run_stop_id is not None or parsed.path == "/api/live-agent-session-runs/stop":
                self._handle_session_run_action("stop", session_run_stop_id)
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
                retry_target_details = _session_run_action_target_details(payload)
                try:
                    if run_id:
                        current_run = live_agent_session_run_controller.get_run(run_id)
                    else:
                        current_run = _latest_session_run_for_action_target(live_agent_session_run_controller, payload)
                        run_id = str(current_run.get("run_id") or "")
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
                        request_overrides={"approve_real_providers": _payload_bool(payload.get("approve_real_providers"))},
                    )
                except (OSError, ValueError) as error:
                    safe_error = _session_ensure_error_message(error)
                    failed_details = {"session_run_id": run_id}
                    failed_details.update({key: value for key, value in retry_target_details.items() if value})
                    record_live_agent_operation(
                        output_root,
                        operation="session_run.retry_now",
                        status="failed",
                        target_id=run_id or retry_target_details["meeting_id"],
                        error=safe_error,
                        details=failed_details,
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, safe_error, details=failed_details)
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
                    _assert_session_run_launch_approved(live_agent_process_supervisor, payload, self._request_server_url())
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
            if parsed.path == "/api/live-agent-create/check":
                payload = self._operation_json_payload(operation="frontend_agent.check")
                if payload is None:
                    return
                try:
                    checked = frontend_live_agent_check_payload(
                        output_root,
                        payload,
                        default_server=self._request_server_url(),
                    )
                except (OSError, ValueError) as error:
                    record_live_agent_operation(
                        output_root,
                        operation="frontend_agent.check",
                        status="failed",
                        target_id=clean_lobby_text(payload.get("meeting_id"), limit=128),
                        error=str(error),
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                record_live_agent_operation(
                    output_root,
                    operation="frontend_agent.check",
                    status=_operation_success_for_result(_operation_result_status(checked.get("status")), success_values={"ok"}),
                    target_id=clean_lobby_text(payload.get("meeting_id"), limit=128),
                    summary="checked frontend-created live agent",
                    details={
                        "provider_id": clean_lobby_text(payload.get("provider_id"), limit=64),
                        "result_status": _operation_result_status(checked.get("status")),
                    },
                )
                self._send_json(checked)
                return
            if parsed.path == "/api/live-agent-create/login":
                if not self._request_is_local_operator():
                    self._send_error(HTTPStatus.FORBIDDEN, "provider login can only be started from the local operator UI")
                    return
                payload = self._operation_json_payload(operation="frontend_agent.login")
                if payload is None:
                    return
                try:
                    login = frontend_live_agent_login_payload(
                        payload,
                        command_launcher=live_agent_login_launcher,
                        command_resolver=live_agent_login_command_resolver,
                    )
                except (OSError, ValueError) as error:
                    record_live_agent_operation(
                        output_root,
                        operation="frontend_agent.login",
                        status="failed",
                        target_id=clean_lobby_text(payload.get("provider_id"), limit=64),
                        error=str(error),
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                record_live_agent_operation(
                    output_root,
                    operation="frontend_agent.login",
                    status="success",
                    target_id=clean_lobby_text(payload.get("provider_id"), limit=64),
                    summary="started provider login from frontend agent modal",
                    details={"provider_id": clean_lobby_text(payload.get("provider_id"), limit=64)},
                )
                self._send_json(login)
                return
            if parsed.path == "/api/room/ensure":
                # Promote a localStorage room to a server-backed meeting on demand
                # (rooms-as-server-objects). Idempotent; safe to call on room open.
                payload = self._operation_json_payload(operation="room.ensure")
                if payload is None:
                    return
                try:
                    meeting_dir = ensure_frontend_meeting(
                        output_root,
                        clean_lobby_text(payload.get("meeting_id"), limit=128),
                        label=clean_lobby_text(payload.get("label"), limit=128),
                    )
                except (OSError, ValueError) as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json({"status": "ready", "meeting_id": meeting_dir.name})
                return
            if parsed.path == "/api/live-agent-create":
                payload = self._operation_json_payload(operation="frontend_agent.create")
                if payload is None:
                    return
                try:
                    created = frontend_live_agent_create_payload(
                        output_root,
                        live_agent_process_supervisor,
                        payload,
                        default_server=self._request_server_url(),
                    )
                except (OSError, ValueError) as error:
                    record_live_agent_operation(
                        output_root,
                        operation="frontend_agent.create",
                        status="failed",
                        target_id=clean_lobby_text(payload.get("meeting_id"), limit=128),
                        error=str(error),
                        details={"provider_id": clean_lobby_text(payload.get("provider_id"), limit=64)},
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                record_live_agent_operation(
                    output_root,
                    operation="frontend_agent.create",
                    status="success" if str(created.get("status") or "") in {"created", "starting"} else "degraded",
                    target_id=str((created.get("agent") or {}).get("agent_id") if isinstance(created.get("agent"), dict) else ""),
                    summary="created frontend live agent",
                    details={
                        "meeting_id": clean_lobby_text(created.get("meeting_id"), limit=128),
                        "provider_id": clean_lobby_text(payload.get("provider_id"), limit=64),
                        "status": clean_lobby_text(created.get("status"), limit=64),
                    },
                )
                self._send_json(created)
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
            if parsed.path == "/api/live-agent-sessions/resume-agent":
                payload = self._operation_json_payload(operation="session.resume_agent")
                if payload is None:
                    return
                agent_id = clean_lobby_text(payload.get("agent_id"), limit=128)
                try:
                    session = live_agent_session_resume_agent_payload(
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
                        operation="session.resume_agent",
                        status="failed",
                        target_id=agent_id or str(safe_details.get("meeting_id") or safe_details.get("requested_meeting_id") or ""),
                        error=safe_error,
                        details={**safe_details, "agent_id": agent_id},
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, safe_error, details={**safe_details, "agent_id": agent_id})
                    return
                record_live_agent_operation(
                    output_root,
                    operation="session.resume_agent",
                    status=_session_start_operation_status(session),
                    target_id=str(session.get("agent_id") or agent_id or session.get("meeting_id") or ""),
                    summary=_session_resume_operation_summary(session),
                    details={**_session_start_operation_details(session), "agent_id": str(session.get("agent_id") or agent_id)},
                )
                self._send_json(session)
                return
            if parsed.path == "/api/live-agent-sessions/agent-timing":
                payload = self._operation_json_payload(operation="session.agent_timing")
                if payload is None:
                    return
                agent_id = clean_lobby_text(payload.get("agent_id"), limit=128)
                try:
                    session = live_agent_session_agent_timing_payload(output_root, payload)
                except (OSError, ValueError) as error:
                    safe_error = str(error)
                    safe_details = _session_start_error_details(payload, error)
                    record_live_agent_operation(
                        output_root,
                        operation="session.agent_timing",
                        status="failed",
                        target_id=agent_id or str(safe_details.get("meeting_id") or safe_details.get("requested_meeting_id") or ""),
                        error=safe_error,
                        details={**safe_details, "agent_id": agent_id},
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, safe_error, details={**safe_details, "agent_id": agent_id})
                    return
                record_live_agent_operation(
                    output_root,
                    operation="session.agent_timing",
                    status="updated",
                    target_id=str(session.get("agent_id") or agent_id),
                    summary=f"poll_interval={session.get('poll_interval')}",
                    details={
                        "agent_id": str(session.get("agent_id") or agent_id),
                        "poll_interval": session.get("poll_interval"),
                        "config_path": str(session.get("config_path") or ""),
                    },
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
            if parsed.path == "/api/live-agent-sessions/stop-agent":
                payload = self._operation_json_payload(operation="session.stop_agent")
                if payload is None:
                    return
                agent_id = clean_lobby_text(payload.get("agent_id"), limit=128)
                try:
                    session = live_agent_session_stop_agent_payload(
                        output_root,
                        live_agent_process_supervisor,
                        payload,
                    )
                except (OSError, ValueError) as error:
                    safe_error = _session_stop_error_message(error)
                    safe_details = _session_start_error_details(payload, error)
                    record_live_agent_operation(
                        output_root,
                        operation="session.stop_agent",
                        status="failed",
                        target_id=agent_id or str(safe_details.get("meeting_id") or safe_details.get("requested_meeting_id") or ""),
                        error=safe_error,
                        details={**safe_details, "agent_id": agent_id},
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, safe_error, details={**safe_details, "agent_id": agent_id})
                    return
                stopped_runs = live_agent_session_run_controller.mark_matching_stopped(
                    meeting_id=str(session.get("meeting_id") or payload.get("meeting_id") or ""),
                    group_id=str(session.get("group_id") or payload.get("group_id") or ""),
                    reason="session.stop_agent",
                )
                if stopped_runs:
                    session["session_runs"] = stopped_runs
                record_live_agent_operation(
                    output_root,
                    operation="session.stop_agent",
                    status=_session_stop_operation_status(session),
                    target_id=str(session.get("agent_id") or agent_id or session.get("meeting_id") or ""),
                    summary=_session_stop_operation_summary(session),
                    details={**_session_stop_operation_details(session), "agent_id": str(session.get("agent_id") or agent_id)},
                )
                self._send_json(session)
                return
            if parsed.path == "/api/live-agent-room/expel":
                payload = self._operation_json_payload(operation="frontend_agent.expel")
                if payload is None:
                    return
                agent_id = clean_lobby_text(payload.get("agent_id"), limit=128)
                try:
                    result = expel_live_agent_from_room_payload(
                        output_root,
                        live_agent_process_supervisor,
                        payload,
                    )
                except (OSError, ValueError) as error:
                    record_live_agent_operation(
                        output_root,
                        operation="frontend_agent.expel",
                        status="failed",
                        target_id=agent_id,
                        error=str(error),
                        details={"meeting_id": clean_lobby_text(payload.get("meeting_id"), limit=128)},
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error), details={"agent_id": agent_id})
                    return
                record_live_agent_operation(
                    output_root,
                    operation="frontend_agent.expel",
                    status="success",
                    target_id=str(result.get("agent_id") or agent_id),
                    summary="expelled frontend live agent from room",
                    details={"meeting_id": str(result.get("meeting_id") or payload.get("meeting_id") or "")},
                )
                self._send_json(result)
                return
            if parsed.path == "/api/live-agent-room/delete-session":
                payload = self._operation_json_payload(operation="frontend_agent.delete_session")
                if payload is None:
                    return
                agent_id = clean_lobby_text(payload.get("agent_id"), limit=128)
                try:
                    result = delete_live_agent_session_payload(
                        output_root,
                        live_agent_process_supervisor,
                        payload,
                    )
                except (OSError, ValueError) as error:
                    record_live_agent_operation(
                        output_root,
                        operation="frontend_agent.delete_session",
                        status="failed",
                        target_id=agent_id,
                        error=str(error),
                        details={"meeting_id": clean_lobby_text(payload.get("meeting_id"), limit=128)},
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error), details={"agent_id": agent_id})
                    return
                record_live_agent_operation(
                    output_root,
                    operation="frontend_agent.delete_session",
                    status="success",
                    target_id=str(result.get("agent_id") or agent_id),
                    summary="deleted frontend live agent session",
                    details={"meeting_id": str(result.get("meeting_id") or payload.get("meeting_id") or "")},
                )
                self._send_json(result)
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
                payload = self._operation_json_payload(
                    operation="live_agent.register",
                    target_id="",
                )
                if payload is None:
                    return
                clean_agent_id = clean_lobby_text(payload.get("agent_id"), limit=64)
                previous_agent = next(
                    (agent for agent in read_live_agents(output_root) if agent.get("agent_id") == clean_agent_id),
                    {},
                )
                try:
                    live_agent = connect_live_agent_payload(output_root, payload)
                except ValueError as error:
                    record_live_agent_operation(
                        output_root,
                        operation="live_agent.register",
                        status="failed",
                        target_id=clean_agent_id,
                        error=str(error),
                        details={"agent_id": clean_agent_id},
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                agent = live_agent.get("agent") if isinstance(live_agent.get("agent"), dict) else {}
                record_live_agent_operation(
                    output_root,
                    operation="live_agent.register",
                    status="success",
                    target_id=str(agent.get("agent_id") or clean_agent_id),
                    summary="registered live agent",
                    details=_live_agent_register_operation_details(
                        output_root,
                        agent,
                        clean_agent_id=clean_agent_id,
                        previous_agent=previous_agent,
                    ),
                )
                self._send_json(live_agent)
                return
            if parsed.path == "/api/public-invite/host-token":
                if get_host_token():
                    provided_host_token = (self.headers.get("X-Host-Token") or "").strip()
                    if not provided_host_token:
                        auth_header = self.headers.get("Authorization") or ""
                        if auth_header.startswith("Bearer "):
                            provided_host_token = auth_header.removeprefix("Bearer ").strip()
                    if not verify_host_token(provided_host_token):
                        if has_runtime_host_token() and self._request_is_local_operator():
                            token = generate_runtime_host_token()
                            self._send_json({
                                "status": "regenerated",
                                "host_token": token,
                                "host_token_configured": True,
                                "public_invite": self._public_invite_status(),
                            })
                            return
                        self._send_error(HTTPStatus.FORBIDDEN, "host token required")
                        return
                    self._send_json({"status": "already_configured", "host_token_configured": True})
                    return
                if not self._request_is_local_operator():
                    self._send_error(HTTPStatus.FORBIDDEN, "host token can only be generated from the local operator UI")
                    return
                if get_public_url():
                    self._send_error(HTTPStatus.FORBIDDEN, "host token must be configured before public URL mode")
                    return
                token = generate_runtime_host_token()
                self._send_json({
                    "status": "generated",
                    "host_token": token,
                    "host_token_configured": True,
                    "public_invite": self._public_invite_status(),
                })
                return
            if parsed.path == "/api/public-invite/public-url":
                if not get_host_token():
                    self._send_error(HTTPStatus.FORBIDDEN, "host token must be configured before public URL")
                    return
                if not self._verify_host_token():
                    return
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
                    public_url = set_runtime_public_url(normalize_public_room_url(str(payload.get("public_url") or "")))
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json({"status": "configured", "public_url": public_url, "public_invite": self._public_invite_status()})
                return
            if parsed.path == "/api/public-invite/tunnel/start":
                generated_host_token = ""
                if not get_host_token():
                    if not self._request_is_local_operator():
                        self._send_error(HTTPStatus.FORBIDDEN, "host token must be configured before starting a public tunnel")
                        return
                    generated_host_token = generate_runtime_host_token()
                if not generated_host_token and not self._verify_host_token():
                    return
                invite_tunnel_manager.set_local_url(self._local_server_url())
                payload = {"status": "ok", "public_invite": self._public_invite_status(invite_tunnel_manager.start())}
                if generated_host_token:
                    payload["host_token"] = generated_host_token
                self._send_json(payload)
                return
            if parsed.path == "/api/public-invite/tunnel/stop":
                if not self._verify_host_token():
                    return
                self._send_json({"status": "ok", "public_invite": self._public_invite_status(invite_tunnel_manager.stop())})
                return
            if parsed.path == "/api/live-agent-join-brief":
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
                    join_brief = live_agent_join_brief_payload(payload, default_server=self._request_server_url())
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json(join_brief)
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
                        else "live-agent remaining official rounds did not fully answer"
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
                        else "live-agent official round did not fully answer"
                    ),
                    details=_turn_round_operation_details(round_result, turn_round_meeting_id),
                )
                self._send_json(round_result)
                return
            turn_preset_meeting_id = _meeting_live_agent_turn_preset_path(parsed.path)
            if turn_preset_meeting_id is not None:
                payload = self._operation_json_payload(operation="official_turn.preset")
                if payload is None:
                    return
                try:
                    preset_result = live_agent_turn_preset_payload(output_root, turn_preset_meeting_id, payload)
                except ValueError as error:
                    record_live_agent_operation(
                        output_root,
                        operation="official_turn.preset",
                        status="failed",
                        target_id=turn_preset_meeting_id,
                        error=str(error),
                        details=_turn_preset_request_operation_details(payload, turn_preset_meeting_id),
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                record_live_agent_operation(
                    output_root,
                    operation="official_turn.preset",
                    status="success" if preset_result.get("status") == "answered" else "degraded",
                    target_id=turn_preset_meeting_id,
                    summary=(
                        "completed live-agent play preset"
                        if preset_result.get("status") == "answered"
                        else "live-agent play preset did not fully answer"
                    ),
                    details=_turn_preset_operation_details(preset_result, turn_preset_meeting_id),
                )
                self._send_json(preset_result)
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
                        else "live-agent official turn sequence did not fully answer"
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
                        **_discovery_operation_details(discoveries, discovery.get("approval_filter")),
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
            if parsed.path == "/api/live-agent-real-session-smoke":
                payload = self._operation_json_payload(operation="session.real_smoke")
                if payload is None:
                    return
                if not _payload_bool(payload.get("approve_real_providers")):
                    safe_details = _real_session_smoke_error_details(payload)
                    record_live_agent_operation(
                        output_root,
                        operation="session.real_smoke",
                        status="failed",
                        target_id=str(safe_details.get("meeting_id") or ""),
                        error=REAL_SESSION_SMOKE_APPROVAL_REQUIRED_MESSAGE,
                        details=safe_details,
                    )
                    self._send_error(
                        HTTPStatus.BAD_REQUEST,
                        REAL_SESSION_SMOKE_APPROVAL_REQUIRED_MESSAGE,
                        details=safe_details,
                    )
                    return
                if not _real_session_smoke_has_explicit_configs(payload):
                    safe_details = _real_session_smoke_error_details(payload)
                    record_live_agent_operation(
                        output_root,
                        operation="session.real_smoke",
                        status="failed",
                        target_id=str(safe_details.get("meeting_id") or ""),
                        error=REAL_SESSION_SMOKE_CONFIG_REQUIRED_MESSAGE,
                        details=safe_details,
                    )
                    self._send_error(
                        HTTPStatus.BAD_REQUEST,
                        REAL_SESSION_SMOKE_CONFIG_REQUIRED_MESSAGE,
                        details=safe_details,
                    )
                    return
                try:
                    smoke = _safe_real_session_smoke_result(
                        live_agent_real_session_smoke_payload(
                            output_root,
                            payload,
                            default_server=self._local_server_url(),
                        )
                    )
                except (LiveAgentSmokeFailed, ValueError, urllib.error.URLError) as error:
                    del error
                    safe_error = "Real session smoke could not be run."
                    safe_details = _real_session_smoke_error_details(payload)
                    record_live_agent_operation(
                        output_root,
                        operation="session.real_smoke",
                        status="failed",
                        target_id=str(safe_details.get("meeting_id") or ""),
                        error=safe_error,
                        details=safe_details,
                    )
                    self._send_error(HTTPStatus.BAD_GATEWAY, safe_error, details=safe_details)
                    return
                result_status = _operation_result_status(smoke.get("status"))
                record_live_agent_operation(
                    output_root,
                    operation="session.real_smoke",
                    status="degraded"
                    if result_status == "degraded"
                    else _operation_success_for_result(result_status, success_values={"ok"}),
                    target_id=str(smoke.get("meeting_id") or payload.get("meeting_id") or ""),
                    summary="ran approved real resident session smoke",
                    details=_real_session_smoke_operation_details(smoke),
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
                        session_run_monitor=session_run_monitor,
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
                        "session_smoke_finalization_status": _operation_result_status(
                            session_smoke.get("finalization_status")
                        ),
                        "session_smoke_finalization_official_event_count": _payload_nonnegative_int(
                            session_smoke.get("finalization_official_event_count"),
                            0,
                        ),
                        "session_smoke_return_packet_event_count": _payload_nonnegative_int(
                            session_smoke.get("return_packet_event_count"),
                            0,
                        ),
                        "session_smoke_artifact_status": _operation_result_status(session_smoke.get("artifact_status")),
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
                shared_memory = official_turn.get("shared_memory") if isinstance(official_turn.get("shared_memory"), dict) else {}
                if shared_memory:
                    reply_details.update(shared_memory)
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
            live_agent_leave_id = _live_agent_action_path(parsed.path, "leave")
            if live_agent_leave_id is not None:
                payload = self._operation_json_payload(
                    operation="live_agent.leave",
                    target_id=live_agent_leave_id,
                    details={"agent_id": clean_lobby_text(live_agent_leave_id, limit=64)},
                )
                if payload is None:
                    return
                try:
                    previous_agent = _live_agent_for_id(output_root, live_agent_leave_id)
                    leave = live_agent_leave_payload(output_root, live_agent_leave_id, payload)
                except ValueError as error:
                    record_live_agent_operation(
                        output_root,
                        operation="live_agent.leave",
                        status="failed",
                        target_id=live_agent_leave_id,
                        error=str(error),
                        details={"agent_id": clean_lobby_text(live_agent_leave_id, limit=64)},
                    )
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                agent = leave.get("agent") if isinstance(leave.get("agent"), dict) else {}
                record_live_agent_operation(
                    output_root,
                    operation="live_agent.leave",
                    status="success",
                    target_id=live_agent_leave_id,
                    summary="marked live agent offline",
                    details={
                        "agent_id": clean_lobby_text(agent.get("agent_id") or live_agent_leave_id, limit=64),
                        "meeting_id": clean_lobby_text(agent.get("meeting_id"), limit=128),
                        "previous_status": clean_lobby_text(previous_agent.get("status"), limit=32),
                        "last_observed_event_id": clean_lobby_text(agent.get("last_observed_event_id"), limit=128),
                        "last_observed_live_event_id": clean_lobby_text(agent.get("last_observed_live_event_id"), limit=128),
                    },
                )
                self._send_json(leave)
                return
            live_agent_dm_reply_id = _live_agent_action_path(parsed.path, "dm-reply")
            if live_agent_dm_reply_id is not None:
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
                    reply = live_agent_dm_reply_payload(output_root, live_agent_dm_reply_id, payload)
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json(reply)
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

        def do_DELETE(self) -> None:
            parsed = urlparse(self.path)
            if not self._request_is_trusted(path=parsed.path, method="DELETE"):
                self._send_error(HTTPStatus.FORBIDDEN, "Untrusted request host or origin")
                return
            query = parse_qs(parsed.query)
            if parsed.path == "/api/room-friends":
                try:
                    deleted = delete_room_friend(output_root, str(query.get("friend_id", [""])[0] or ""))
                except ValueError as error:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._send_json(
                    {
                        "deleted": deleted,
                        **room_friends_payload(output_root, read_live_agents(output_root)),
                    }
                )
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

        def _extract_session_token(self) -> str:
            """Extract bearer session token from Authorization header."""
            auth = self.headers.get("Authorization") or ""
            if auth.startswith("Bearer "):
                return auth.removeprefix("Bearer ").strip()
            return ""

        def _verify_host_token(self) -> bool:
            """Check host token from X-Host-Token header or Authorization Bearer.

            Sends 403 and returns False if verification fails.
            Returns True if allowed (either token matches or no gate configured).
            """
            token = (self.headers.get("X-Host-Token") or "").strip()
            if not token:
                # Fall back to Authorization header for host endpoints
                auth = self.headers.get("Authorization") or ""
                if auth.startswith("Bearer "):
                    token = auth.removeprefix("Bearer ").strip()
            if not verify_host_token(token):
                self._send_error(HTTPStatus.FORBIDDEN, "host token required")
                return False
            return True

        def _request_uses_loopback_host(self) -> bool:
            host_name, _ = _split_authority_host_port(str(self.headers.get("Host") or ""))
            return host_name in _LOOPBACK_HOSTNAMES

        def _request_is_local_operator(self) -> bool:
            return (
                _is_loopback_host(self.server.server_address[0])
                and self._request_uses_loopback_host()
                and _origin_is_loopback_or_empty(self.headers.get("Origin"))
            )

        def _public_invite_status(self, tunnel_status: dict[str, object] | None = None) -> dict[str, object]:
            tunnel = tunnel_status or invite_tunnel_manager.status()
            token_configured = bool(get_host_token())
            return {
                "host_token_configured": token_configured,
                "host_gate_required": host_gate_required(),
                "public_url": get_public_url(),
                "tunnel": tunnel,
                "can_generate_host_token": (
                    (not token_configured and not bool(get_public_url()))
                    or (has_runtime_host_token() and self._request_is_local_operator())
                ),
            }

        def _local_server_url(self) -> str:
            return _local_server_url(self.server.server_address)

        def _handle_session_run_action(self, command: str, path_run_id: str | None) -> None:
            operation = f"session_run.{command}"
            payload = self._operation_json_payload(operation=operation, target_id=path_run_id or "")
            if payload is None:
                return
            run_id = path_run_id or str(payload.get("run_id") or "").strip()
            target_details = _session_run_action_target_details(payload)
            try:
                if not run_id:
                    target_run = _latest_session_run_for_action_target(live_agent_session_run_controller, payload)
                    run_id = str(target_run.get("run_id") or "")
                if command == "pause":
                    session_run = live_agent_session_run_controller.pause_run(run_id)
                    response_status = "paused"
                    summary = "paused durable live-agent session run"
                elif command == "resume":
                    session_run = live_agent_session_run_controller.resume_run(run_id)
                    response_status = "resumed"
                    summary = "resumed durable live-agent session run"
                elif command == "stop":
                    session_run = live_agent_session_run_controller.stop_run(run_id, reason="operator_stop")
                    response_status = "stopped"
                    summary = "stopped durable live-agent session run"
                else:
                    raise ValueError(f"Unsupported session-run action: {command}")
            except (OSError, ValueError) as error:
                safe_error = _session_ensure_error_message(error)
                failed_details = {"session_run_id": run_id}
                failed_details.update({key: value for key, value in target_details.items() if value})
                record_live_agent_operation(
                    output_root,
                    operation=operation,
                    status="failed",
                    target_id=run_id or target_details["meeting_id"],
                    error=safe_error,
                    details=failed_details,
                )
                self._send_error(HTTPStatus.BAD_REQUEST, safe_error, details=failed_details)
                return

            operation_details = {
                "session_run_id": str(session_run.get("run_id") or run_id),
                "meeting_id": str(session_run.get("meeting_id") or ""),
                "group_id": str(session_run.get("group_id") or ""),
                "run_status": str(session_run.get("status") or ""),
                "phase": str(session_run.get("phase") or ""),
            }
            if command == "pause":
                operation_details["paused_status"] = str(session_run.get("paused_status") or "")
            record_live_agent_operation(
                output_root,
                operation=operation,
                status="success",
                target_id=str(session_run.get("run_id") or run_id),
                summary=summary,
                details=operation_details,
            )
            self._send_json({"status": response_status, "session_run": session_run})

        def _send_react_app_index(self, frontend_root: Path) -> None:
            index_path = frontend_root / "index.html"
            if not frontend_dist_status(frontend_root).static_available:
                self._send_error(HTTPStatus.SERVICE_UNAVAILABLE, REACT_APP_MISSING_BUILD_MESSAGE)
                return
            html = index_path.read_text(encoding="utf-8")
            data = _rewrite_react_app_index(html).encode("utf-8")
            self._send_bytes(data, "text/html; charset=utf-8", cache_control="no-cache")

        def _send_file(
            self,
            path: Path,
            content_type: str | None = None,
            *,
            cache_control: str = "no-store",
        ) -> None:
            if not path.exists() or not path.is_file():
                self._send_error(HTTPStatus.NOT_FOUND, "File not found")
                return
            guessed = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            data = path.read_bytes()
            self._send_bytes(data, guessed, cache_control=cache_control)

        def _send_bytes(self, data: bytes, content_type: str, *, cache_control: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache_control)
            self.end_headers()
            self.wfile.write(data)

        def _send_attachment_file(self, path: Path, metadata: dict[str, object], *, inline: bool) -> None:
            if not path.exists() or not path.is_file():
                self._send_error(HTTPStatus.NOT_FOUND, "Attachment not found")
                return
            filename = str(metadata.get("filename") or path.name)
            content_type = str(metadata.get("content_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            safe_inline = inline and content_type in INLINE_SAFE_IMAGE_TYPES
            data = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", attachment_content_disposition(filename, inline=safe_inline))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, payload: dict[str, object]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self._send_public_invite_cors_headers()
            self.end_headers()
            self.wfile.write(data)

        def _send_sse_snapshot(self, event_name: str, payload: dict[str, object]) -> None:
            data = _sse_event(event_name, payload, event_id=_last_payload_event_id(payload))
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self._send_public_invite_cors_headers()
            self.end_headers()
            self.wfile.write(data)

        def _handle_ws_upgrade(self, query: dict) -> None:
            """WS 전환 (WS-4): upgrade /ws, bind the ticket's session as a fixed
            identity, then run the per-connection frame loop. Delivery reuses the
            SSE snapshot reader (no pub/sub yet); the win here is the governed
            handshake (identity + client_type fixed once)."""
            import select

            if not is_websocket_upgrade(self.headers):
                self._send_error(HTTPStatus.BAD_REQUEST, "WebSocket upgrade required")
                return
            ticket = (query.get("ticket") or [""])[0]
            session = ws_ticket_store.consume(ticket)
            if not session:
                self._send_error(HTTPStatus.UNAUTHORIZED, "invalid or expired ws ticket")
                return
            key = str(self.headers.get("Sec-WebSocket-Key") or "")
            if not key:
                self._send_error(HTTPStatus.BAD_REQUEST, "missing Sec-WebSocket-Key")
                return
            identity = {
                "agent_id": str(session.get("agent_id") or ""),
                "display_name": str(session.get("display_name") or ""),
                "participant_type": str(session.get("participant_type") or "human"),
                "client_type": str(session.get("client_type") or session.get("connection_kind") or "browser"),
                "invite_scope": str(session.get("invite_scope") or "read_write"),
                "meeting_id": str(session.get("meeting_id") or ""),
                "operator": bool(session.get("operator")),
            }
            self.close_connection = True
            self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", compute_accept_key(key))
            self.end_headers()
            self.wfile.flush()
            ws = WsRoomSession(identity=identity, deps=_ws_room_deps())
            assembler = MessageAssembler()
            sock = self.connection
            def _send_all(frames: list[bytes]) -> bool:
                # Deliver outbound frames; a send failure (client already gone)
                # must NOT abort processing — the important side effects (append,
                # status) already ran in handle_frame. Returns False if the peer
                # is gone so the loop can wind down after the batch is processed.
                for frame in frames:
                    try:
                        sock.sendall(frame)
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        return False
                return True

            try:
                while not ws.closed:
                    ready, _, _ = select.select([sock], [], [], SSE_EVENT_POLL_INTERVAL_SECONDS)
                    if ready:
                        data = sock.recv(65536)
                        if not data:
                            break  # client closed the TCP connection
                        assembler.feed(data)
                        # Process EVERY received frame first (side effects: say
                        # append, thinking status) — then send. A say followed by
                        # an immediate client close must still append.
                        outbound: list[bytes] = []
                        for opcode, payload in assembler.messages():
                            outbound.extend(ws.handle_frame(opcode, payload))
                        if not _send_all(outbound):
                            break
                    if not _send_all(ws.poll()):
                        break
            except WebSocketProtocolError:
                try:
                    sock.sendall(encode_close(CLOSE_PROTOCOL_ERROR))
                except OSError:
                    pass
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

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
            self._send_public_invite_cors_headers()
            self.end_headers()
            current_last_event_id = last_event_id
            current_payload_signature: str | None = None
            last_write_at = 0.0
            while True:
                try:
                    payload = _stream_snapshot_payload(
                        output_root,
                        stream,
                        meeting_id=meeting_id,
                        last_event_id=current_last_event_id,
                    )
                    latest_event_id = _last_payload_event_id(payload)
                    wrote_frame = False
                    if latest_event_id:
                        self.wfile.write(_sse_event(event_name, payload, event_id=latest_event_id))
                        current_last_event_id = latest_event_id
                        current_payload_signature = _payload_signature(payload)
                        wrote_frame = True
                    elif _payload_signature(payload) and _payload_signature(payload) != current_payload_signature:
                        self.wfile.write(_sse_event(event_name, payload))
                        current_payload_signature = _payload_signature(payload)
                        wrote_frame = True
                    elif time.monotonic() - last_write_at >= SSE_KEEPALIVE_INTERVAL_SECONDS:
                        self.wfile.write(b": keep-alive\n\n")
                        wrote_frame = True
                    if wrote_frame:
                        self.wfile.flush()
                        last_write_at = time.monotonic()
                    time.sleep(SSE_EVENT_POLL_INTERVAL_SECONDS)
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
            self._send_public_invite_cors_headers()
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


_REACT_APP_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".ico": "image/x-icon",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
}


def _react_app_content_type(path: Path) -> str:
    return _REACT_APP_CONTENT_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _react_app_cache_control(path: Path) -> str:
    return "no-cache"


def _rewrite_react_app_index(html: str) -> str:
    return html.replace('src="/assets/', 'src="/app/assets/').replace('href="/assets/', 'href="/app/assets/')
