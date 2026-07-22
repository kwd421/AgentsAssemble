from __future__ import annotations

import json
import math
import re
import threading
import time
import urllib.error
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble.room.attachments import (
    AttachmentError,
    normalize_attachment_references,
)
from agentsassemble.legacy.live_agent.codex_sessions import list_codex_sessions
from agentsassemble.features.mafia.routes import register_mafia_routes
from agentsassemble.features.side_chat.routes import register_side_chat_routes
from agentsassemble.features.social.routes import (
    register_room_friend_profile_routes,
)
from agentsassemble.legacy.live_agent.runtime.context import live_agent_context_contract
from agentsassemble.legacy.admission_projection import LiveAgentLegacyAdmissionProjection
from agentsassemble.live_agent_flow import FLOW_TERMINAL_EVENT_TYPES, FlowOptions, flow_turn_count
from agentsassemble.web.routes.providers import (
    model_catalog_payload,
    provider_catalog_payload,
    register_provider_routes,
)
from agentsassemble.web.routes.gui import register_current_gui_routes
from agentsassemble.application.gui import ApplicationDatabase, GuiApplicationServices
from agentsassemble.application.gui_runtime import GuiRuntimeDependencies, serve_gui_runtime
from agentsassemble.application.gui_factory import (
    GuiRuntimeConstructors,
    build_gui_application_services,
)
from agentsassemble.web.routes.attachments import register_attachment_routes
from agentsassemble.legacy.live_agent.http.flow import register_live_agent_flow_routes
from agentsassemble.legacy.gui_application import (
    LegacyGuiApplication,
)
from agentsassemble.legacy.gui_hooks import (
    build_legacy_gui_patch_hooks,
    register_legacy_gui_routes,
)
from agentsassemble.web.routes.observability import register_observability_routes
from agentsassemble.web.routes.public_invite import register_public_invite_admin_routes
from agentsassemble.legacy.meeting.http.room_composition import _local_agent_session_turn_adapter, register_room_routes
from agentsassemble.web.routes.room_settings import register_room_settings_routes
from agentsassemble.web.static import (
    ReactStaticTransport,
    safe_static_path as _safe_static_path,
)
from agentsassemble.web.response import (
    GuiResponseMethods,
    _last_payload_event_id,
    _rewrite_react_app_index,
    _sse_event,
)
from agentsassemble.web.security import (
    _LOOPBACK_HOSTNAMES,
    _PUBLIC_INVITE_CORS_HEADERS,
    _PUBLIC_INVITE_CORS_METHODS,
    _host_header_is_trusted,
    _is_loopback_host,
    _origin_is_trusted,
    _origin_matches_public_url,
    _public_invite_route_allowed,
    _request_trusted,
    _split_authority_host_port,
)
from agentsassemble.web.routes.retired import register_retired_legacy_routes
from agentsassemble.web.router import (
    GuiDeps,
    RequestContext,
    Router,
    local_server_url as _local_server_url,
)
from agentsassemble.web.websocket import handle_ws_upgrade, register_ws_ticket_route
from agentsassemble.web.gui_server import make_gui_http_handler
from agentsassemble.room.realtime import (
    RoomCommandRejected,
    RoomRealtimeController,
)
from agentsassemble.application.session_run_monitor import (
    PeriodicSessionRunMonitor,
    safe_monitor_error_type,
)
from agentsassemble.legacy.live_agent.runtime.join_brief import live_agent_join_brief_payload
from agentsassemble.legacy.live_agent.runtime.launch_policy import APPROVAL_REQUIRED_MESSAGE, assert_resident_launch_approved
from agentsassemble.live_agent_runner import load_group_configs
from agentsassemble.legacy.live_agent.runtime.roster import filter_live_agent_roster, safe_live_agent_roster_payload
from agentsassemble.legacy.live_agent.health import (
    DEFAULT_SESSION_RUN_MONITOR_INTERVAL_SECONDS,
    MIN_SESSION_RUN_MONITOR_INTERVAL_SECONDS,
    safe_health_identity as _safe_session_run_health_identity,
    safe_process_group_id as _safe_process_group_id,
)
from agentsassemble.legacy.live_agent.health_queries import live_agent_health_payload
from agentsassemble.legacy.live_agent.discovery import (
    discovery_operation_details as _discovery_operation_details,
    live_agent_discovery_payload,
)
from agentsassemble.legacy.live_agent.observation_health import (
    latest_live_agent_turn_request_for_agent as _latest_live_agent_turn_request_for_agent,
    latest_lobby_event as _latest_lobby_event,
    live_agent_live_observation_status as _live_agent_live_observation_status,
    live_agent_lobby_observation_status as _live_agent_lobby_observation_status,
    live_agent_observation_events as _live_agent_observation_events,
)
from agentsassemble.legacy.live_agent.process_control import (
    process_bulk_offline_operation_details as _process_bulk_offline_operation_details,
    process_offline_operation_details as _process_offline_operation_details,
    process_recover_error_message as _process_recover_error_message,
    process_restart_error_message as _process_restart_error_message,
    process_start_error_message as _process_start_error_message,
    process_stop_error_message as _process_stop_error_message,
    process_stop_running_error_message as _process_stop_running_error_message,
    process_stop_running_operation_status as _process_stop_running_operation_status,
)
from agentsassemble.legacy.live_agent.session_control import (
    session_check_operation_status as _session_check_operation_status,
    session_check_operation_summary as _session_check_operation_summary,
    session_ensure_error_message as _session_ensure_error_message,
    session_ensure_operation_summary as _session_ensure_operation_summary,
    session_recover_error_message as _session_recover_error_message,
    session_recover_operation_summary as _session_recover_operation_summary,
    session_restart_error_message as _session_restart_error_message,
    session_restart_operation_summary as _session_restart_operation_summary,
    session_resume_error_message as _session_resume_error_message,
    session_resume_operation_summary as _session_resume_operation_summary,
    session_start_error_details as _session_start_error_details,
    session_start_error_message as _session_start_error_message,
    session_start_operation_summary as _session_start_operation_summary,
    session_stop_error_message as _session_stop_error_message,
    session_stop_operation_status as _session_stop_operation_status,
    session_stop_operation_summary as _session_stop_operation_summary,
)
from agentsassemble.legacy.live_agent.session_projection import (
    session_check_operation_details as _session_check_operation_details,
    session_start_operation_details as _session_start_operation_details,
    session_stop_operation_details as _session_stop_operation_details,
)
from agentsassemble.legacy.live_agent.session_run_service import LegacySessionRunActions
from agentsassemble.legacy.live_agent.runtime.settings import (
    update_live_agent_config_options,
    update_live_agent_config_poll_interval,
)
from agentsassemble.legacy.live_agent.state import (
    heartbeat_live_agent,
    read_live_agents,
    update_live_agent_cooldown,
    update_live_agent_engagement,
    update_live_agent_options,
    update_live_agent_poll_interval,
)
from agentsassemble.legacy.live_agent.runtime.operations import append_live_agent_operation
from agentsassemble.legacy.live_agent.runtime.processes import (
    LiveAgentProcessSupervisor,
)
from agentsassemble.legacy.live_agent.runtime.probe import run_live_agent_probe, safe_probe_timeout
from agentsassemble.legacy.live_agent.runtime.sessions import (
    recover_live_agent_session,
    restart_live_agent_session,
    resume_live_agent_session_agent,
    resume_live_agent_session,
    session_ensure_action,
    start_live_agent_session,
    stop_live_agent_session_agent,
    stop_live_agent_session,
)
from agentsassemble.legacy.live_agent.runtime.session_runs import LiveAgentSessionRunController
from agentsassemble.legacy.live_agent.runtime.smoke import (
    LiveAgentSmokeFailed,
    run_live_agent_official_round_smoke,
    run_live_agent_real_session_smoke,
    run_live_agent_session_smoke,
    run_live_agent_smoke,
)
from agentsassemble.legacy.meeting.support.lobby_queries import (
    LOBBY_HISTORY_MAX_PAGE_LIMIT,
    LOBBY_HISTORY_PAGE_LIMIT,
    read_lobby,
    read_lobby_before,
)
from agentsassemble.legacy.meeting.lobby_commands import (
    send_lobby_message_to_remote_bridge as _send_legacy_lobby_message_to_remote_bridge,
)
from agentsassemble.legacy.live_agent.queries import (
    LIVE_AGENT_ROOM_LOBBY_EVENT_LIMIT,
    live_agent_return_packet_payload,
    live_agent_room_payload,
    require_live_agent as _live_agent_for_id,
)
from agentsassemble.legacy.live_agent.diagnostics import (
    live_agent_operations_payload,
    live_agent_process_events_payload,
    live_agent_session_check_payload,
    live_agent_session_readiness_payload,
    live_agent_session_runs_payload,
    session_process_groups_snapshot as _session_process_groups_snapshot,
)
from agentsassemble.legacy.live_agent.process_projection import (
    live_agent_processes_payload,
    parse_public_timestamp as _parse_public_timestamp,
    process_payload_with_agent_connection_evidence as _process_payload_with_agent_connection_evidence,
)
from agentsassemble.legacy.live_agent.preflight import (
    live_agent_preflight_payload,
)
from agentsassemble.legacy.live_agent.readiness import (
    live_agent_readiness_payload as _resident_live_agent_readiness_payload,
)
from agentsassemble.legacy.live_agent.readiness_projection import (
    readiness_health_operation_details as _readiness_health_operation_details,
    safe_readiness_probe_result as _safe_readiness_probe_result,
)
from agentsassemble.legacy.live_agent.smoke import (
    LegacyLiveAgentSmokeService,
    live_agent_real_session_smoke_payload as _resident_live_agent_real_session_smoke_payload,
    live_agent_session_smoke_payload as _resident_live_agent_session_smoke_payload,
    official_round_smoke_operation_details as _official_round_smoke_operation_details,
    real_session_smoke_error_details as _real_session_smoke_error_details,
    real_session_smoke_has_explicit_configs as _real_session_smoke_has_explicit_configs,
    real_session_smoke_operation_details as _real_session_smoke_operation_details,
    request_json as _request_json,
    safe_real_session_smoke_result as _safe_real_session_smoke_result,
    session_smoke_error_details as _session_smoke_error_details,
    session_smoke_operation_details as _session_smoke_operation_details,
    session_smoke_soak_cycle_count as _payload_session_smoke_soak_cycle_count,
    session_smoke_soak_interval_seconds as _payload_session_smoke_soak_interval_seconds,
)
from agentsassemble.legacy.live_agent.roster_queries import (
    live_agent_roster_admission_details as _live_agent_roster_admission_details,
    live_agent_roster_with_admission_evidence as _live_agent_roster_with_admission_evidence,
    live_agent_without_quota_fields as _live_agent_without_quota_fields,
    live_agents_payload,
)
from agentsassemble.legacy.live_agent.presence import (
    connect_live_agent_payload,
    live_agent_heartbeat_payload,
    live_agent_leave_payload,
)
from agentsassemble.legacy.live_agent.engagement import (
    update_live_agent_engagement_payload,
)
from agentsassemble.legacy.live_agent.probe import (
    live_agent_probe_payload,
)
from agentsassemble.legacy.live_agent.official_reply import (
    live_agent_official_turn_payload,
)
from agentsassemble.legacy.live_agent.speech import (
    LegacyLiveAgentLobbySpeechDeps,
    LegacyLiveAgentSpeechService,
    flow_turn_conflict as _flow_turn_conflict,
    live_agent_lobby_flow_metadata as _live_agent_lobby_flow_metadata,
)
from agentsassemble.legacy.meeting.queries import (
    build_meeting_payload,
    build_meeting_stream_payload,
    build_workroom_queue_payload,
    list_meetings,
    project_meeting_stream_events,
)
from agentsassemble.legacy.meeting.lifecycle import (
    live_agent_finalize_meeting_payload,
    live_agent_meeting_start_payload,
)
from agentsassemble.legacy.meeting.records import (
    live_agent_admission_details as _live_agent_admission_details_from_meeting,
    read_meeting_record as _read_meeting_record,
    safe_meeting_dir as _safe_meeting_dir,
)
from agentsassemble.legacy.meeting.official_turns import (
    live_agent_turn_call_payload,
    live_agent_turn_request_payload,
    live_agent_turn_sequence_payload,
)
from agentsassemble.legacy.meeting.official_rounds import (
    _live_agent_turn_rounds_payload_locked,
    _payload_bounded_round_count,
    live_agent_turn_preset_payload,
    live_agent_turn_round_payload,
    live_agent_turn_rounds_payload,
    rounds_finalization_result_if_requested as _rounds_finalization_result_if_requested,
    skipped_rounds_finalization_result as _skipped_rounds_finalization_result,
)
from agentsassemble.legacy.meeting.review_checkpoint import create_review_checkpoint as _create_review_checkpoint
from agentsassemble.legacy_codex_session_compat import (
    codex_session_invite_payload,
    codex_session_join_payload as _legacy_codex_session_join_payload,
)
from agentsassemble.legacy.meeting.core.runner import run_demo_meeting
from agentsassemble.diagnostics.provider_health import provider_health_payload, provider_health_report
from agentsassemble.legacy.live_agent.provider_login import ProviderLoginService
from agentsassemble.application.public_tunnel import PublicTunnelManager
from agentsassemble.application.public_invite_runtime import PublicInviteRuntime
from agentsassemble.web.frontend_runtime import (
    REACT_APP_BUILD_COMMAND,
    REACT_APP_MISSING_BUILD_MESSAGE,
    default_frontend_dist_root,
    frontend_dist_status,
)
from agentsassemble.features.social.direct_messages import enqueue_room_friend_direct_dm
from agentsassemble.identity.factory import build_identity_repository
from agentsassemble.identity.repository import IdentityBackend
from agentsassemble.persistence.local.identity.registry import (
    identity_store_for_output_root,
)
from agentsassemble.room.moderation import is_room_member_muted
from agentsassemble.room.members import mark_thinking, room_members_payload
from agentsassemble.room.repository import RoomRepository
from agentsassemble.application.room_repository_factory import (
    DEFAULT_POSTGRES_DSN_ENV,
    RoomRepositorySettings,
    build_postgres_application_database,
    build_room_repository,
)
from agentsassemble.admission.repository import InviteSessionRepository
from agentsassemble.admission.repository_factory import build_invite_session_repository
from agentsassemble.room.speech import (
    ActorIdentity,
    GovernedLobbySayRejected,
    governed_lobby_say,
)
from agentsassemble.web.room_session import (
    WsRoomDeps,
    WsSayRejected,
)
from agentsassemble.application.agent_sessions import enqueue_agent_session_auto_turn_for_lobby_event, room_sse_frames_after_cursor
from agentsassemble.admission.invite import (
    compatibility_public_invite_runtime,
)
from agentsassemble.legacy.meeting.core.events import (
    FLOW_METADATA_KEYS,
    ROOM_TOPIC_LIMIT,
    append_live_event,
    append_lobby_event_to_file,
    clean_lobby_text,
    read_live_events_after,
    read_lobby_events_after,
    read_side_chat_events_after,
)
from agentsassemble.features.side_chat.service import (
    _filter_side_chat_events_for_meeting,
)
from agentsassemble.web.sse_cadence import (
    SSE_EVENT_POLL_INTERVAL_SECONDS,
    SSE_KEEPALIVE_INTERVAL_SECONDS,
)

SSE_ERROR_MESSAGE_LIMIT = 500
REMOTE_LOBBY_REQUESTER = None
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
        raise RuntimeError("Play/free flow is disabled; use turn-based Agent Sessions.")

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
LIVE_AGENT_LOBBY_LOCK = threading.RLock()
REAL_SESSION_SMOKE_REDACTED_SOURCE_EVENT_IDS: set[str] = set()
SESSION_RUN_MONITOR_ERROR = "Live-agent session run monitor failed."
SESSION_ENSURE_REASON_RESIDENT_SESSION_ID_DRIFT = "resident_session_id_drift"
SESSION_ENSURE_REASON_STALE_LOBBY_OBSERVATION = "stale_lobby_observation"
SESSION_ENSURE_REASON_STALE_LIVE_OBSERVATION = "stale_live_observation"
SESSION_ENSURE_REASONS = {
    SESSION_ENSURE_REASON_RESIDENT_SESSION_ID_DRIFT,
    SESSION_ENSURE_REASON_STALE_LOBBY_OBSERVATION,
    SESSION_ENSURE_REASON_STALE_LIVE_OBSERVATION,
}


def _backfill_room_registry(
    output_root: Path,
    identity_backend: IdentityBackend,
) -> None:
    """Register pre-existing meeting dirs into the rooms table.

    This remains best-effort compatibility behavior so a legacy directory
    cannot block current server startup.
    """

    try:
        known = {
            str(room.get("room_id"))
            for room in identity_backend.list_rooms(include_archived=True)
        }
        owner = identity_backend.operator_user_id()
        for meeting in list_meetings(output_root):
            meeting_id = str(meeting.get("meeting_id") or "")
            if not meeting_id or meeting_id in known:
                continue
            identity_backend.upsert_room(
                room_id=meeting_id,
                owner_id=owner,
                label=str(meeting.get("topic") or ""),
                origin="backfill",
            )
    except Exception:
        return


def _build_gui_application_services(
    output_root: Path,
    *,
    process_supervisor: LiveAgentProcessSupervisor | None = None,
    session_run_controller: LiveAgentSessionRunController | None = None,
    session_run_monitor: LiveAgentSessionRunMonitor | None = None,
    flow_supervisor: LiveAgentFlowSupervisor | None = None,
    public_tunnel_manager: PublicTunnelManager | None = None,
    room_realtime_controller_override: RoomRealtimeController | None = None,
    room_repository_override: RoomRepository | None = None,
    owns_room_repository_override: bool = False,
    invite_repository_override: InviteSessionRepository | None = None,
    owns_invite_repository_override: bool = False,
    identity_backend_override: IdentityBackend | None = None,
    owns_identity_backend_override: bool = False,
    application_database_override: ApplicationDatabase | None = None,
    owns_application_database_override: bool = False,
    public_invite_runtime_override: PublicInviteRuntime | None = None,
    attention_shadow_mode: str = "off",
) -> GuiApplicationServices:
    """Select concrete GUI runtimes and delegate ownership composition."""

    return build_gui_application_services(
        output_root,
        constructors=GuiRuntimeConstructors(
            process_supervisor=LiveAgentProcessSupervisor,
            session_run_controller=LiveAgentSessionRunController,
            flow_supervisor=LiveAgentFlowSupervisor,
            public_invite_runtime=PublicInviteRuntime,
            public_tunnel_manager=PublicTunnelManager,
            session_run_monitor=LiveAgentSessionRunMonitor,
            legacy_admission_projection=LiveAgentLegacyAdmissionProjection,
            backfill_room_registry=_backfill_room_registry,
        ),
        process_supervisor=process_supervisor,
        session_run_controller=session_run_controller,
        session_run_monitor=session_run_monitor,
        flow_supervisor=flow_supervisor,
        public_tunnel_manager=public_tunnel_manager,
        room_realtime_controller_override=room_realtime_controller_override,
        room_repository_override=room_repository_override,
        owns_room_repository_override=owns_room_repository_override,
        invite_repository_override=invite_repository_override,
        owns_invite_repository_override=owns_invite_repository_override,
        identity_backend_override=identity_backend_override,
        owns_identity_backend_override=owns_identity_backend_override,
        application_database_override=application_database_override,
        owns_application_database_override=owns_application_database_override,
        public_invite_runtime_override=public_invite_runtime_override,
        attention_shadow_mode=attention_shadow_mode,
    )


def serve_gui(
    host: str = "127.0.0.1",
    port: int = 8765,
    output_root: Path | None = None,
    *,
    room_repository_backend: str = "sqlite",
    room_postgres_dsn_env: str = DEFAULT_POSTGRES_DSN_ENV,
    attention_shadow_mode: str = "off",
    public_url: str = "",
    host_token: str = "",
    unsafe_expose_control_plane: bool = False,
    start_public_tunnel: bool = False,
    live_agent_config: Path | None = None,
    live_agent_group_id: str = "",
    live_agent_auto_restart: bool = False,
    live_agent_max_restarts: int = 0,
    live_agent_restart_backoff_seconds: float = 5.0,
    live_agent_stale_restart_after_seconds: float = 0.0,
    frontend_dist_root: Path | None = None,
) -> None:
    serve_gui_runtime(
        dependencies=GuiRuntimeDependencies(
            is_loopback_host=_is_loopback_host,
            room_repository_settings=RoomRepositorySettings.from_environment,
            build_postgres_application_database=build_postgres_application_database,
            build_room_repository=build_room_repository,
            build_invite_session_repository=build_invite_session_repository,
            build_identity_repository=build_identity_repository,
            build_application_services=lambda *args, **kwargs: _build_gui_application_services(
                *args,
                **kwargs,
            ),
            make_handler=lambda *args, **kwargs: _make_handler(*args, **kwargs),
            server_factory=lambda *args, **kwargs: ThreadingHTTPServer(*args, **kwargs),
            local_server_url=_local_server_url,
            autostart_live_agent_group=lambda *args, **kwargs: _autostart_live_agent_group(
                *args,
                **kwargs,
            ),
            print_startup_banner=lambda *args, **kwargs: _print_gui_startup_banner(
                *args,
                **kwargs,
            ),
        ),
        host=host,
        port=port,
        output_root=output_root,
        room_repository_backend=room_repository_backend,
        room_postgres_dsn_env=room_postgres_dsn_env,
        attention_shadow_mode=attention_shadow_mode,
        public_url=public_url,
        host_token=host_token,
        unsafe_expose_control_plane=unsafe_expose_control_plane,
        start_public_tunnel=start_public_tunnel,
        live_agent_config=live_agent_config,
        live_agent_group_id=live_agent_group_id,
        live_agent_auto_restart=live_agent_auto_restart,
        live_agent_max_restarts=live_agent_max_restarts,
        live_agent_restart_backoff_seconds=live_agent_restart_backoff_seconds,
        live_agent_stale_restart_after_seconds=live_agent_stale_restart_after_seconds,
        frontend_dist_root=frontend_dist_root,
    )


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


class LiveAgentSessionRunMonitor(PeriodicSessionRunMonitor):
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
        super().__init__(
            reconcile_runs=lambda: _reconcile_live_agent_session_runs(
                self.output_root,
                self.process_supervisor,
                self.session_run_controller,
                default_server=self.default_server,
                summary="reconciled durable live-agent session runs during GUI runtime",
            ),
            report_failure=self._report_failure,
            interval_seconds=interval_seconds,
            default_interval_seconds=DEFAULT_SESSION_RUN_MONITOR_INTERVAL_SECONDS,
            minimum_interval_seconds=MIN_SESSION_RUN_MONITOR_INTERVAL_SECONDS,
        )

    def _report_failure(self, error: Exception) -> None:
        error_type = safe_monitor_error_type(error)
        record_live_agent_operation(
            self.output_root,
            operation="session_run.monitor",
            status="failed",
            summary="live-agent session-run monitor failed",
            error=SESSION_RUN_MONITOR_ERROR,
            details={"error_type": error_type},
        )


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
    identity_backend: IdentityBackend | None = None,
) -> dict[str, object]:
    with LIVE_AGENT_LOBBY_LOCK:
        appended = append_lobby_event_to_file(
            output_root / "lobby.jsonl",
            event,
            live_agent_endpoint=live_agent_endpoint,
            allow_flow_metadata=allow_flow_metadata,
        )
    room_id = clean_lobby_text(appended.get("flow_meeting_id"), limit=128)
    if room_id:
        try:
            (identity_backend or identity_store_for_output_root(output_root)).touch_room(
                room_id
            )
        except Exception:
            pass
    return appended


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
    *,
    repository: RoomRepository | None = None,
    sessions: list[dict[str, object]] | None = None,
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
            sessions=list(sessions or []),
            repository=repository,
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
    return _send_legacy_lobby_message_to_remote_bridge(
        output_root,
        message,
        meeting_id=meeting_id,
        target_agent_id=target_agent_id,
        speaker_name=speaker_name,
        requester=REMOTE_LOBBY_REQUESTER,
        append_lobby_event=append_lobby_event,
        public_lobby_allows_room_scope=_public_lobby_allows_room_scope,
        is_muted=is_room_member_muted,
    )


def codex_sessions_payload(limit: int = 20) -> dict[str, object]:
    return {"sessions": list_codex_sessions(limit=limit)}


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


def live_agent_session_agent_options_payload(
    output_root: Path,
    payload: dict[str, object],
) -> dict[str, object]:
    """Edit an existing agent's permission_option / fast_mode (post-creation).

    Writes both the room agent record and, when a saved group config is known, the
    config file so the change survives a RESUME/START. Takes effect on next launch
    — a running resident keeps its launch-time config until restarted."""
    agent_id = clean_lobby_text(payload.get("agent_id"), limit=64)
    if not agent_id:
        raise ValueError("Agent id is required.")
    if not any(str(agent.get("agent_id") or "") == agent_id for agent in read_live_agents(output_root)):
        raise ValueError(f"Live agent {agent_id} was not found.")
    has_permission = "permission_option" in payload
    has_fast = "fast_mode" in payload
    if not has_permission and not has_fast:
        raise ValueError("Nothing to update: provide permission_option and/or fast_mode.")
    permission_option = payload.get("permission_option") if has_permission else None
    fast_mode = payload.get("fast_mode") if has_fast else None

    live_agent_config_path = str(
        payload.get("live_agent_config_path") or payload.get("live_agent_config") or ""
    ).strip()
    config_result: dict[str, object] = {}
    if live_agent_config_path:
        config_result = update_live_agent_config_options(
            Path(live_agent_config_path),
            agent_id,
            permission_option=permission_option,
            fast_mode=fast_mode,
        )
    agent = update_live_agent_options(
        output_root,
        agent_id,
        permission_option=permission_option,
        fast_mode=fast_mode,
    )
    return {
        "status": "updated",
        "agent_id": agent_id,
        "permission_option": agent.get("permission_option"),
        "fast_mode": bool(agent.get("fast_mode")),
        "config_path": str(config_result.get("config_path") or live_agent_config_path),
        "applies_on": "next_start",
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


def _legacy_live_agent_speech_service(output_root: Path) -> LegacyLiveAgentSpeechService:
    return LegacyLiveAgentSpeechService(
        output_root,
        lobby=LegacyLiveAgentLobbySpeechDeps(
            append_lobby_event=lambda *args, **kwargs: append_lobby_event(*args, **kwargs),
            public_lobby_allows_room_scope=lambda payload: _public_lobby_allows_room_scope(payload),
            is_muted=lambda *args, **kwargs: is_room_member_muted(*args, **kwargs),
            lobby_lock=LIVE_AGENT_LOBBY_LOCK,
            is_smoke_source_redacted=lambda source_event_id: (
                source_event_id in REAL_SESSION_SMOKE_REDACTED_SOURCE_EVENT_IDS
            ),
            redact_smoke_events=lambda root, source_event_ids: (
                _redact_real_session_smoke_lobby_events(root, source_event_ids)
            ),
            smoke_reply_message=lambda source_event_id, message: (
                _real_session_smoke_reply_message(source_event_id, message)
            ),
            smoke_reply_redaction=REAL_SESSION_SMOKE_REPLY_REDACTION,
        ),
    )


def live_agent_dm_reply_payload(
    output_root: Path,
    agent_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    return _legacy_live_agent_speech_service(output_root).post_dm_reply(agent_id, payload)


def _history_page_limit(query: dict[str, list[str]]) -> int:
    try:
        return int(str(query.get("limit", [""])[0] or LOBBY_HISTORY_PAGE_LIMIT))
    except (TypeError, ValueError):
        return LOBBY_HISTORY_PAGE_LIMIT


def _pre_join_guide_payload(
    server_url: str,
    *,
    public_url: str = "",
) -> dict[str, object]:
    """Machine-readable join manual served from GET /join (Accept: application/json).

    Lets an AI client go from invite link to participation without downloading
    or reverse-engineering the SPA bundle.
    """
    base = (public_url or server_url).rstrip("/")
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


def _api_catalog_payload(
    server_url: str,
    *,
    public_url: str = "",
) -> dict[str, object]:
    """Minimal API self-description (friend feedback #2: 403s everywhere told
    a new client nothing)."""
    base = (public_url or server_url).rstrip("/")
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


def live_agent_lobby_message_payload(
    output_root: Path,
    agent_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    return _legacy_live_agent_speech_service(output_root).post_lobby_message(agent_id, payload)


def live_agent_review_checkpoint_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    meeting_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    return _create_review_checkpoint(
        output_root,
        process_supervisor,
        meeting_id,
        payload,
        turn_requester=live_agent_turn_request_payload,
    )


def live_agent_smoke_payload(payload: dict[str, object], *, default_server: str) -> dict[str, object]:
    """Compatibility seam used by aggregate readiness until that route moves."""
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
    """Compatibility seam used by aggregate readiness until that route moves."""
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
    """Compatibility seam used by aggregate readiness until that route moves."""
    return _resident_live_agent_session_smoke_payload(
        output_root,
        payload,
        default_server=default_server,
        request_json=_request_json,
        runner=run_live_agent_session_smoke,
    )


def live_agent_real_session_smoke_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    """Compatibility seam retained for direct imports and tests."""
    return _resident_live_agent_real_session_smoke_payload(
        output_root,
        payload,
        default_server=default_server,
        request_json=_request_json,
        runner=run_live_agent_real_session_smoke,
    )


def live_agent_readiness_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
    session_run_monitor: LiveAgentSessionRunMonitor | None = None,
) -> dict[str, object]:
    smoke = LegacyLiveAgentSmokeService(
        output_root,
        basic_smoke_runner=lambda **kwargs: run_live_agent_smoke(**kwargs),
        official_round_smoke_runner=lambda **kwargs: run_live_agent_official_round_smoke(**kwargs),
        session_smoke_runner=lambda **kwargs: run_live_agent_session_smoke(**kwargs),
        real_session_smoke_runner=lambda **kwargs: run_live_agent_real_session_smoke(**kwargs),
    )
    return _resident_live_agent_readiness_payload(
        output_root,
        process_supervisor,
        payload,
        default_server=default_server,
        session_run_monitor=session_run_monitor,
        smoke=smoke,
        probe_runner=lambda *args, **kwargs: run_live_agent_probe(*args, **kwargs),
    )


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


def codex_session_join_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    return _legacy_codex_session_join_payload(
        output_root,
        process_supervisor,
        payload,
        default_server=default_server,
        ensure_session=live_agent_session_ensure_payload,
        restart_session=live_agent_session_restart_payload,
    )


def _index_by_id(items: object) -> dict[str, dict[str, object]]:
    return {str(item["id"]): item for item in _as_dict_list(items) if item.get("id")}


def _as_dict_list(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


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


def _operation_result_status(value: object) -> str:
    return str(value or "unknown").strip() or "unknown"


def _operation_success_for_result(value: object, *, success_values: set[str]) -> str:
    return "success" if _operation_result_status(value) in success_values else "failed"


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


def _print_gui_startup_banner(
    server_url: str,
    *,
    frontend_dist_root: Path | None = None,
    room_repository_backend: str = "sqlite",
) -> None:
    base_url = server_url.rstrip("/")
    dist_status = frontend_dist_status(frontend_dist_root)
    print(f"AgentsAssemble GUI: {base_url}")
    print(f"- Room repository: {room_repository_backend}")
    if dist_status.static_available:
        print(f"- Operator console (default): {base_url}/ (React)")
        print(f"- Same Discord room client alias: {base_url}/app/")
    else:
        print(f"- Operator console unavailable until the React build exists: {base_url}/")
        print(f"- Build React for the default console: {REACT_APP_BUILD_COMMAND}")
        print(f"- Same Discord room client alias: {base_url}/app/ (build required)")


def _make_handler(
    output_root: Path,
    *,
    application_services: GuiApplicationServices | None = None,
    process_supervisor: LiveAgentProcessSupervisor | None = None,
    session_run_controller: LiveAgentSessionRunController | None = None,
    session_run_monitor: LiveAgentSessionRunMonitor | None = None,
    flow_supervisor: LiveAgentFlowSupervisor | None = None,
    frontend_dist_root: Path | None = None,
    public_tunnel_manager: PublicTunnelManager | None = None,
    live_agent_login_launcher: object | None = None,
    live_agent_login_command_resolver: object | None = None,
    room_realtime_controller_override: RoomRealtimeController | None = None,
    room_repository_override: RoomRepository | None = None,
    invite_repository_override: InviteSessionRepository | None = None,
    public_invite_runtime_override: PublicInviteRuntime | None = None,
    attention_shadow_mode: str = "off",
    legacy_session_run_actions_override: LegacySessionRunActions | None = None,
) -> type[BaseHTTPRequestHandler]:
    react_app_root = (frontend_dist_root or default_frontend_dist_root()).resolve()
    resolved_public_invite_runtime = public_invite_runtime_override
    if application_services is None and resolved_public_invite_runtime is None:
        resolved_public_invite_runtime = compatibility_public_invite_runtime()
    services = application_services or _build_gui_application_services(
        output_root,
        process_supervisor=process_supervisor,
        session_run_controller=session_run_controller,
        session_run_monitor=session_run_monitor,
        flow_supervisor=flow_supervisor,
        public_tunnel_manager=public_tunnel_manager,
        room_realtime_controller_override=room_realtime_controller_override,
        room_repository_override=room_repository_override,
        invite_repository_override=invite_repository_override,
        public_invite_runtime_override=resolved_public_invite_runtime,
        attention_shadow_mode=attention_shadow_mode,
    )

    def require_same_service(name: str, override: object | None, actual: object) -> None:
        if override is not None and override is not actual:
            raise ValueError(f"{name} override does not match the GUI application services instance.")

    if application_services is not None:
        if output_root.resolve() != services.output_root.resolve():
            raise ValueError("GUI application services were built for a different output root.")
        require_same_service("process supervisor", process_supervisor, services.process_supervisor)
        require_same_service("session run controller", session_run_controller, services.session_run_controller)
        require_same_service("session run monitor", session_run_monitor, services.session_run_monitor)
        require_same_service("flow supervisor", flow_supervisor, services.flow_supervisor)
        require_same_service("public tunnel manager", public_tunnel_manager, services.public_tunnel_manager)
        require_same_service(
            "room realtime controller",
            room_realtime_controller_override,
            services.room_realtime_controller,
        )
        require_same_service("room repository", room_repository_override, services.room_repository)
        require_same_service(
            "invite repository",
            invite_repository_override,
            services.invite_repository,
        )
        require_same_service(
            "public invite runtime",
            public_invite_runtime_override,
            services.public_invite,
        )

    live_agent_process_supervisor = services.process_supervisor
    live_agent_session_run_controller = services.session_run_controller
    # Single-use tickets bind a verified session to a /ws open. Browsers cannot
    # set Authorization on ``new WebSocket``.
    ws_ticket_store = services.ws_ticket_store
    room_realtime_controller = services.room_realtime_controller
    room_repository = services.room_repository

    def append_server_lobby_event(
        event_output_root: Path,
        event: dict[str, object],
        *,
        live_agent_endpoint: bool = False,
        allow_flow_metadata: bool = False,
    ) -> dict[str, object]:
        return append_lobby_event(
            event_output_root,
            event,
            live_agent_endpoint=live_agent_endpoint,
            allow_flow_metadata=allow_flow_metadata,
            identity_backend=services.identity_backend,
        )

    def _ws_room_deps(channel, handler) -> WsRoomDeps:
        # Reuse the proven SSE snapshot machinery + the governed say append path,
        # so the WS transport behaves exactly like the HTTP/SSE one (no pub/sub yet).
        def read_lobby_after(meeting_id: str, after_id: str) -> tuple[list, str]:
            payload = _stream_snapshot_payload(
                output_root,
                "lobby",
                meeting_id=meeting_id,
                last_event_id=after_id or None,
                repository=room_repository,
            )
            events = list(payload.get("events", []))
            return events, (_last_payload_event_id(payload) or after_id)

        def read_roster(meeting_id: str) -> tuple[list, str]:
            payload = _stream_snapshot_payload(
                output_root,
                "roster",
                meeting_id=meeting_id,
                last_event_id=None,
                repository=room_repository,
                sessions=services.sessions.active_summary(),
            )
            return list(payload.get("members", [])), str(_payload_signature(payload) or "")

        def read_side_chat_after(meeting_id: str, after_id: str) -> tuple[list, str]:
            payload = _stream_snapshot_payload(
                output_root,
                "side_chat",
                meeting_id=meeting_id,
                last_event_id=after_id or None,
                repository=room_repository,
            )
            events = list(payload.get("events", []))
            return events, (_last_payload_event_id(payload) or after_id)

        def post_say(identity: dict, payload: dict) -> dict:
            try:
                resolved = lobby_payload_with_attachments(output_root, dict(payload))
            except AttachmentError as error:
                raise WsSayRejected(str(error), category="bad_message") from error
            try:
                event = governed_lobby_say(
                    output_root,
                    identity=ActorIdentity.from_mapping(identity),
                    payload=resolved,
                    append_lobby_event=append_server_lobby_event,
                    public_lobby_allows_room_scope=_public_lobby_allows_room_scope,
                    is_muted=is_room_member_muted,
                )
                enqueue_agent_session_auto_turn_for_lobby_event(
                    output_root,
                    event,
                    turn_adapter=_local_agent_session_turn_adapter,
                    repository=room_repository,
                )
                return event
            except GovernedLobbySayRejected as rejected:
                raise WsSayRejected(str(rejected), category=rejected.category) from rejected

        def set_thinking(identity: dict, on: bool) -> None:
            # Ephemeral "generating a reply" flag → the roster carries a `thinking`
            # bool that the roster push delivers, lighting up the typing indicator.
            mark_thinking(
                str(identity.get("meeting_id") or ""),
                str(identity.get("agent_id") or ""),
                on,
            )

        def execute_command(identity: dict, message: dict) -> dict[str, object]:
            try:
                return room_realtime_controller.handle_command(
                    identity,
                    message,
                    server_url=_local_server_url(handler.server.server_address),
                    ticket_issuer=lambda bridge_identity: ws_ticket_store.issue(bridge_identity),
                )
            except RoomCommandRejected as rejected:
                from agentsassemble.web.room_session import WsCommandRejected

                raise WsCommandRejected(str(rejected), code=rejected.code) from rejected

        return WsRoomDeps(
            read_lobby_after=read_lobby_after,
            read_roster=read_roster,
            read_side_chat_after=read_side_chat_after,
            post_say=post_say,
            is_muted=lambda meeting_id, agent_id: is_room_member_muted(output_root, meeting_id, agent_id),
            set_thinking=set_thinking,
            is_session_active=lambda session_token: bool(services.sessions.verify(session_token)),
            room_snapshot=lambda identity, after_seq: room_realtime_controller.snapshot(
                identity,
                after_seq=after_seq,
            ),
            execute_command=execute_command,
            on_subscribe=lambda identity, streams, after_seq: channel.subscribe(streams),
        )
    # R2: route-table dispatcher. Migrated domains register here; do_GET/do_POST
    # try the table first and fall back to the legacy if-chains below.
    route_deps = GuiDeps(
        output_root=output_root,
        room_repository=room_repository,
        identity_backend=services.identity_backend,
        invite_application=services.invites,
        room_sessions=services.sessions,
        admission_preflight_service=services.admission_preflight,
        admission_coordinator=services.admission,
        operator_pairing_service=services.pairing,
        public_invite_runtime=services.public_invite,
        attachment_store=services.media_store,
        legacy_admission_projection=services.legacy_admission_projection,
        process_supervisor=live_agent_process_supervisor,
        read_lobby=read_lobby,
        read_lobby_before=read_lobby_before,
        append_lobby_event=append_server_lobby_event,
        lobby_payload_with_attachments=lobby_payload_with_attachments,
        public_lobby_allows_room_scope=_public_lobby_allows_room_scope,
        history_page_limit=_history_page_limit,
        read_legacy_agents=read_live_agents,
    )
    route_table = Router()

    def _read_operation_payload(
        ctx: RequestContext,
        operation_name: str,
        target_id: str = "",
    ) -> dict[str, object] | None:
        def record_invalid_json() -> None:
            record_live_agent_operation(
                output_root,
                operation=operation_name,
                status="failed",
                target_id=target_id,
                error="Invalid JSON",
                details={},
            )

        return ctx.read_json_body(
            before_invalid_json_response=record_invalid_json,
        )

    def _legacy_session_run_should_reconcile(
        run: dict[str, object],
        *,
        target_run_id: str,
    ) -> bool:
        return _session_run_monitor_should_reconcile(
            output_root,
            live_agent_process_supervisor,
            run,
            target_run_id=target_run_id,
        )

    def _legacy_session_run_reconcile(
        *,
        default_server: str,
        target_run_id: str,
        approve_real_providers: bool,
    ) -> list[dict[str, object]]:
        return _reconcile_live_agent_session_runs(
            output_root,
            live_agent_process_supervisor,
            live_agent_session_run_controller,
            default_server=default_server,
            summary="retried durable live-agent session run immediately",
            target_run_id=target_run_id,
            request_overrides={"approve_real_providers": approve_real_providers},
        )

    def _legacy_session_run_assert_launch_approved(
        payload: dict[str, object],
        *,
        default_server: str,
    ) -> None:
        _assert_session_run_launch_approved(
            live_agent_process_supervisor,
            payload,
            default_server,
        )

    legacy_application = LegacyGuiApplication(
        output_root=output_root,
        processes=live_agent_process_supervisor,
        session_runs=live_agent_session_run_controller,
        session_run_monitor=session_run_monitor,
        room_repository=room_repository,
        append_lobby_event=append_server_lobby_event,
        public_lobby_allows_room_scope=_public_lobby_allows_room_scope,
        is_muted=is_room_member_muted,
        remote_lobby_requester=lambda: REMOTE_LOBBY_REQUESTER,
        turn_adapter=lambda: _local_agent_session_turn_adapter,
        read_operation_payload=_read_operation_payload,
        record_operation=record_live_agent_operation,
        speech=_legacy_live_agent_speech_service(output_root),
        hooks=build_legacy_gui_patch_hooks(
            turn_request=live_agent_turn_request_payload,
            provider_health_report=lambda *args, **kwargs: provider_health_report(
                *args,
                **kwargs,
            ),
            probe=lambda *args, **kwargs: run_live_agent_probe(*args, **kwargs),
            basic_smoke=lambda *args, **kwargs: run_live_agent_smoke(*args, **kwargs),
            official_round_smoke=lambda *args, **kwargs: run_live_agent_official_round_smoke(
                *args,
                **kwargs,
            ),
            session_smoke=lambda *args, **kwargs: run_live_agent_session_smoke(
                *args,
                **kwargs,
            ),
            real_session_smoke=lambda *args, **kwargs: run_live_agent_real_session_smoke(
                *args,
                **kwargs,
            ),
            session_start=lambda *args, **kwargs: live_agent_session_start_payload(*args, **kwargs),
            session_ensure=lambda *args, **kwargs: live_agent_session_ensure_payload(*args, **kwargs),
            session_resume=lambda *args, **kwargs: live_agent_session_resume_payload(*args, **kwargs),
            session_resume_agent=lambda *args, **kwargs: live_agent_session_resume_agent_payload(*args, **kwargs),
            session_agent_timing=lambda *args, **kwargs: live_agent_session_agent_timing_payload(*args, **kwargs),
            session_agent_options=lambda *args, **kwargs: live_agent_session_agent_options_payload(*args, **kwargs),
            session_check=lambda *args, **kwargs: live_agent_session_check_payload(*args, **kwargs),
            session_restart=lambda *args, **kwargs: live_agent_session_restart_payload(*args, **kwargs),
            session_recover=lambda *args, **kwargs: live_agent_session_recover_payload(*args, **kwargs),
            session_stop=lambda *args, **kwargs: live_agent_session_stop_payload(*args, **kwargs),
            session_stop_agent=lambda *args, **kwargs: live_agent_session_stop_agent_payload(*args, **kwargs),
            process_start=lambda *args, **kwargs: start_live_agent_process_payload(*args, **kwargs),
            process_stop_running=lambda *args, **kwargs: stop_running_live_agent_processes_payload(*args, **kwargs),
            process_stop=lambda *args, **kwargs: stop_live_agent_process_payload(*args, **kwargs),
            process_restart=lambda *args, **kwargs: restart_live_agent_process_payload(*args, **kwargs),
            process_recover=lambda *args, **kwargs: recover_live_agent_process_payload(*args, **kwargs),
            session_run_should_reconcile=_legacy_session_run_should_reconcile,
            session_run_reconcile=_legacy_session_run_reconcile,
            session_run_assert_launch_approved=_legacy_session_run_assert_launch_approved,
            session_run_ensure=lambda payload, *, default_server: live_agent_session_ensure_payload(
                output_root,
                live_agent_process_supervisor,
                payload,
                default_server=default_server,
            ),
        ),
        session_run_actions_override=legacy_session_run_actions_override,
    )

    def _room_friend_direct_dm(ctx: RequestContext, payload: dict[str, object]) -> dict[str, object]:
        return room_friend_direct_dm_payload(
            ctx.deps.output_root,
            ctx.deps.process_supervisor,
            payload,
            default_server=ctx.request_server_url(),
        )

    register_current_gui_routes(
        route_table,
        services=services,
        provider_login_service=ProviderLoginService(
            output_root=output_root,
            command_launcher=live_agent_login_launcher,
            command_resolver=live_agent_login_command_resolver,
        ),
        post_direct_dm=_room_friend_direct_dm,
        read_operation_payload=_read_operation_payload,
        record_operation=record_live_agent_operation,
    )
    register_legacy_gui_routes(
        route_table,
        legacy_application=legacy_application,
        flow=services.flow_supervisor,
        read_operation_payload=_read_operation_payload,
        record_operation=record_live_agent_operation,
    )

    static_transport = ReactStaticTransport(
        frontend_root=react_app_root,
        pre_join_guide_payload=lambda server_url: _pre_join_guide_payload(
            server_url,
            public_url=services.public_invite.public_url(),
        ),
        api_catalog_payload=lambda server_url: _api_catalog_payload(
            server_url,
            public_url=services.public_invite.public_url(),
        ),
    )

    return make_gui_http_handler(
        output_root=output_root,
        services=services,
        route_table=route_table,
        route_deps=route_deps,
        static_transport=static_transport,
        ws_ticket_store=ws_ticket_store,
        room_realtime_controller=room_realtime_controller,
        ws_room_deps_factory=_ws_room_deps,
        room_repository=room_repository,
        stream_snapshot_payload=lambda *args, **kwargs: _stream_snapshot_payload(
            *args,
            **kwargs,
        ),
        room_sse_frames_after_cursor=lambda *args, **kwargs: room_sse_frames_after_cursor(
            *args,
            **kwargs,
        ),
        sse_stream_error_payload=lambda *args, **kwargs: _sse_stream_error_payload(
            *args,
            **kwargs,
        ),
        sse_frame_id=_sse_frame_id,
        payload_signature=_payload_signature,
    )


def _sse_frame_id(frame: str) -> str:
    for line in frame.splitlines():
        if line.startswith("id:"):
            return line.removeprefix("id:").strip()
    return ""


def _payload_signature(payload: dict[str, object]) -> str | None:
    signature = payload.get("payload_signature")
    return signature if isinstance(signature, str) and signature else None
