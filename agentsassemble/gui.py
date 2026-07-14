from __future__ import annotations

import json
import math
import mimetypes
import re
import threading
import time
import urllib.error
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

from agentsassemble.attachments import (
    AttachmentError,
    FileAttachmentStore,
    normalize_attachment_references,
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
from agentsassemble.config import load_council_config
from agentsassemble.live_agent_context import live_agent_context_contract, live_agent_context_contract_with_join_semantics
from agentsassemble.live_agent_flow import FLOW_SPEAKING_ACTIONS, FLOW_TERMINAL_EVENT_TYPES, FlowOptions, flow_turn_count
from agentsassemble.live_agent_frontend_create import (
    frontend_live_agent_check_payload,
    frontend_live_agent_create_payload,
    frontend_live_agent_options_payload,
)
from agentsassemble.provider_sessions import list_provider_sessions
from agentsassemble.gui_provider_http import (
    model_catalog_payload,
    provider_catalog_payload,
    register_provider_routes,
)
from agentsassemble.gui_application import GuiApplicationServices
from agentsassemble.gui_attachment_http import register_attachment_routes
from agentsassemble.gui_mafia_http import register_mafia_routes
from agentsassemble.gui_live_agent_flow_http import register_live_agent_flow_routes
from agentsassemble.gui_legacy_lobby_http import register_legacy_lobby_routes
from agentsassemble.gui_legacy_meeting_http import register_legacy_meeting_routes
from agentsassemble.gui_legacy_live_agent_read_http import (
    LegacyLiveAgentReadDeps,
    register_legacy_live_agent_read_routes,
)
from agentsassemble.gui_legacy_live_agent_process_http import (
    LegacyProcessHttpDeps,
    register_legacy_process_mutation_routes,
)
from agentsassemble.gui_legacy_live_agent_discovery_http import (
    LegacyLiveAgentDiscoveryHttpDeps,
    register_legacy_live_agent_discovery_route,
)
from agentsassemble.gui_legacy_live_agent_preflight_http import (
    LegacyLiveAgentPreflightHttpDeps,
    register_legacy_live_agent_preflight_route,
)
from agentsassemble.gui_legacy_live_agent_readiness_http import (
    LegacyLiveAgentReadinessHttpDeps,
    register_legacy_live_agent_readiness_route,
)
from agentsassemble.gui_legacy_live_agent_self_managed_http import register_legacy_self_managed_agent_routes
from agentsassemble.gui_legacy_live_agent_smoke_http import (
    LegacyLiveAgentSmokeHttpDeps,
    register_legacy_live_agent_smoke_routes,
)
from agentsassemble.gui_legacy_live_agent_session_http import (
    LegacySessionHttpDeps,
    register_legacy_session_mutation_routes,
)
from agentsassemble.gui_legacy_live_agent_session_run_http import (
    LegacySessionRunHttpDeps,
    register_legacy_session_run_basic_routes,
)
from agentsassemble.gui_observability_http import register_observability_routes
from agentsassemble.gui_public_invite_http import register_public_invite_admin_routes
from agentsassemble.gui_room_http import _local_agent_session_turn_adapter, register_room_routes
from agentsassemble.gui_room_settings_http import register_room_settings_routes
from agentsassemble.gui_side_chat_http import register_side_chat_routes
from agentsassemble.gui_social_http import register_room_friend_profile_routes
from agentsassemble.gui_response import (
    GuiResponseMethods,
    _last_payload_event_id,
    _rewrite_react_app_index,
    _sse_event,
)
from agentsassemble.gui_request_security import (
    _LOOPBACK_HOSTNAMES,
    _PUBLIC_INVITE_CORS_HEADERS,
    _PUBLIC_INVITE_CORS_METHODS,
    _host_header_is_trusted,
    _is_loopback_host,
    _origin_is_loopback_or_empty,
    _origin_is_trusted,
    _origin_matches_public_url,
    _public_invite_route_allowed,
    _request_trusted,
    _split_authority_host_port,
)
from agentsassemble.gui_router import GuiDeps, RequestContext, Router
from agentsassemble.gui_ws_http import handle_ws_upgrade, register_ws_ticket_route
from agentsassemble.room_bridge_process import NativeCliBridgeProcessManager
from agentsassemble.room_realtime import (
    RoomCommandRejected,
    RoomRealtimeController,
    default_native_cli_provider_specs,
)
from agentsassemble.session_run_monitor import PeriodicSessionRunMonitor, safe_monitor_error_type
from agentsassemble.live_agent_join_brief import build_live_agent_join_brief
from agentsassemble.live_agent_room_admin import (
    delete_live_agent_session_payload,
    expel_live_agent_from_room_payload,
)
from agentsassemble.live_agent_self_managed import LegacySelfManagedAgentService
from agentsassemble.live_agent_timing import DEFAULT_LIVE_AGENT_POLL_INTERVAL
from agentsassemble.live_agent_launch_policy import APPROVAL_REQUIRED_MESSAGE, assert_resident_launch_approved
from agentsassemble.live_agent_runner import load_group_configs
from agentsassemble.live_agent_roster import filter_live_agent_roster, safe_live_agent_roster_payload
from agentsassemble.legacy_live_agent_health import (
    DEFAULT_SESSION_RUN_MONITOR_INTERVAL_SECONDS,
    MIN_SESSION_RUN_MONITOR_INTERVAL_SECONDS,
    safe_health_identity as _safe_session_run_health_identity,
    safe_process_group_id as _safe_process_group_id,
)
from agentsassemble.legacy_live_agent_health_queries import (
    LegacyLiveAgentHealthQueryService,
    live_agent_health_payload,
)
from agentsassemble.legacy_live_agent_discovery import (
    LegacyLiveAgentDiscoveryService,
    discovery_operation_details as _discovery_operation_details,
    live_agent_discovery_payload,
)
from agentsassemble.legacy_live_agent_observation_health import (
    latest_live_agent_turn_request_for_agent as _latest_live_agent_turn_request_for_agent,
    latest_lobby_event as _latest_lobby_event,
    live_agent_live_observation_status as _live_agent_live_observation_status,
    live_agent_lobby_observation_status as _live_agent_lobby_observation_status,
    live_agent_observation_events as _live_agent_observation_events,
)
from agentsassemble.legacy_live_agent_process_control import (
    process_bulk_offline_operation_details as _process_bulk_offline_operation_details,
    process_offline_operation_details as _process_offline_operation_details,
    process_recover_error_message as _process_recover_error_message,
    process_restart_error_message as _process_restart_error_message,
    process_start_error_message as _process_start_error_message,
    process_stop_error_message as _process_stop_error_message,
    process_stop_running_error_message as _process_stop_running_error_message,
    process_stop_running_operation_status as _process_stop_running_operation_status,
)
from agentsassemble.diagnostic_report_projection import (
    safe_diagnostic_report_payload as _safe_diagnostic_report_payload,
)
from agentsassemble.legacy_live_agent_process_service import (
    LegacyLiveAgentProcessMutationService,
    LegacyProcessMutationActions,
)
from agentsassemble.legacy_live_agent_session_control import (
    session_check_error_message as _session_check_error_message,
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
    session_start_operation_status as _session_start_operation_status,
    session_start_operation_summary as _session_start_operation_summary,
    session_stop_error_message as _session_stop_error_message,
    session_stop_operation_status as _session_stop_operation_status,
    session_stop_operation_summary as _session_stop_operation_summary,
)
from agentsassemble.legacy_live_agent_session_projection import (
    session_check_operation_details as _session_check_operation_details,
    session_start_operation_details as _session_start_operation_details,
    session_stop_operation_details as _session_stop_operation_details,
)
from agentsassemble.legacy_live_agent_session_service import (
    LegacyLiveAgentSessionMutationService,
    LegacySessionMutationActions,
)
from agentsassemble.legacy_live_agent_session_run_service import (
    LegacyLiveAgentSessionRunMutationService,
    LegacySessionRunActions,
)
from agentsassemble.live_agent_settings import (
    update_live_agent_config_options,
    update_live_agent_config_poll_interval,
)
from agentsassemble.live_agents import (
    connect_live_agent,
    heartbeat_live_agent,
    read_live_agents,
    update_live_agent_cooldown,
    update_live_agent_engagement,
    update_live_agent_options,
    update_live_agent_poll_interval,
)
from agentsassemble.live_agent_operations import append_live_agent_operation
from agentsassemble.live_agent_meetings import start_live_agent_meeting
from agentsassemble.live_agent_finalization import finalize_live_agent_meeting
from agentsassemble.live_agent_processes import (
    LiveAgentProcessSupervisor,
    clean_live_agent_group_id,
)
from agentsassemble.live_agent_probe import run_live_agent_probe, safe_probe_timeout
from agentsassemble.live_agent_play_presets import build_play_preset_turns
from agentsassemble.live_agent_review_checkpoints import write_review_checkpoint_artifacts
from agentsassemble.live_agent_rounds import build_official_round_turns, completed_official_round_ids, remaining_official_round_ids
from agentsassemble.live_agent_sessions import (
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
    run_live_agent_official_round_smoke,
    run_live_agent_real_session_smoke,
    run_live_agent_session_smoke,
    run_live_agent_smoke,
)
from agentsassemble.live_agent_turns import (
    is_official_turn_reply_event,
    is_review_checkpoint_reply_event,
    official_turn_cancellation,
    wait_for_official_turn_reply,
    wait_for_review_checkpoint_reply,
)
from agentsassemble.lobby_queries import (
    LOBBY_HISTORY_MAX_PAGE_LIMIT,
    LOBBY_HISTORY_PAGE_LIMIT,
    read_lobby,
    read_lobby_before,
)
from agentsassemble.legacy_lobby_commands import (
    LegacyLobbyCommandService,
    send_lobby_message_to_remote_bridge as _send_legacy_lobby_message_to_remote_bridge,
)
from agentsassemble.live_meeting_memory import (
    write_live_meeting_memory_artifacts,
)
from agentsassemble.legacy_live_agent_queries import (
    LIVE_AGENT_ROOM_LOBBY_EVENT_LIMIT,
    LegacyLiveAgentQueryService,
    live_agent_return_packet_payload,
    live_agent_room_payload,
    live_events_visible_to_agent as _live_events_visible_to_agent,
    require_live_agent as _live_agent_for_id,
)
from agentsassemble.legacy_live_agent_diagnostics import (
    LegacyLiveAgentDiagnosticQueryService,
    live_agent_operations_payload,
    live_agent_process_events_payload,
    live_agent_session_check_payload,
    live_agent_session_readiness_payload,
    live_agent_session_runs_payload,
    session_process_groups_snapshot as _session_process_groups_snapshot,
)
from agentsassemble.legacy_live_agent_process_projection import (
    live_agent_processes_payload,
    parse_public_timestamp as _parse_public_timestamp,
    process_payload_with_agent_connection_evidence as _process_payload_with_agent_connection_evidence,
)
from agentsassemble.legacy_live_agent_preflight import (
    LegacyLiveAgentPreflightService,
    live_agent_preflight_payload,
)
from agentsassemble.legacy_live_agent_readiness import (
    LegacyLiveAgentReadinessService,
    live_agent_readiness_payload as _resident_live_agent_readiness_payload,
)
from agentsassemble.legacy_live_agent_readiness_projection import (
    readiness_health_operation_details as _readiness_health_operation_details,
    safe_readiness_probe_result as _safe_readiness_probe_result,
)
from agentsassemble.legacy_live_agent_smoke import (
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
from agentsassemble.legacy_live_agent_roster_queries import (
    LegacyLiveAgentRosterQueryService,
    live_agent_register_admission_details as _live_agent_register_admission_details,
    live_agent_roster_admission_details as _live_agent_roster_admission_details,
    live_agent_roster_with_admission_evidence as _live_agent_roster_with_admission_evidence,
    live_agent_without_quota_fields as _live_agent_without_quota_fields,
    live_agents_payload,
)
from agentsassemble.legacy_meeting_queries import (
    LegacyMeetingQueryService,
    build_meeting_payload,
    build_meeting_stream_payload,
    build_workroom_queue_payload,
    list_meetings,
    project_meeting_stream_events,
)
from agentsassemble.legacy_meeting_records import (
    live_agent_admission_details as _live_agent_admission_details_from_meeting,
    read_meeting_record as _read_meeting_record,
    safe_meeting_dir as _safe_meeting_dir,
)
from agentsassemble.meeting import run_demo_meeting
from agentsassemble.provider_health import provider_health_report
from agentsassemble.provider_login import ProviderLoginService
from agentsassemble.public_tunnel import PublicTunnelManager
from agentsassemble.frontend_runtime import (
    REACT_APP_BUILD_COMMAND,
    REACT_APP_MISSING_BUILD_MESSAGE,
    default_frontend_dist_root,
    frontend_dist_status,
)
from agentsassemble.room_friend_dms import (
    append_live_agent_dm_reply,
    enqueue_room_friend_direct_dm,
)
from agentsassemble.identity_store import default_identity_db_path, identity_store_for_output_root
from agentsassemble.room_members import is_room_member_muted, mark_thinking, room_members_payload
from agentsassemble.room_repository import RoomRepository
from agentsassemble.room_repository_factory import (
    DEFAULT_POSTGRES_DSN_ENV,
    RoomRepositorySettings,
    build_room_repository,
)
from agentsassemble.room_store import RoomStore
from agentsassemble.room_speech import (
    ActorIdentity,
    GovernedLobbySayRejected,
    ensure_lobby_say_allowed,
    governed_official_reply,
    governed_lobby_say,
)
from agentsassemble.ws_room_session import (
    WsRoomDeps,
    WsSayRejected,
    WsTicketStore,
)
from agentsassemble.room_users import (
    configure_room_users_store,
    list_rooms,
    operator_user_id,
    touch_room,
    upsert_room,
)
from agentsassemble.agent_sessions import enqueue_agent_session_auto_turn_for_lobby_event, room_sse_frames_after_cursor
from agentsassemble.room_invite import (
    active_sessions_summary,
    configure_room_invite_store,
    default_room_invite_store_path,
    generate_runtime_host_token,
    get_host_token,
    get_public_url,
    set_runtime_host_token,
    set_runtime_public_url,
    verify_host_token,
    verify_session_token,
)
from agentsassemble.meeting_events import (
    FLOW_METADATA_KEYS,
    LOBBY_KINDS,
    ROOM_TOPIC_LIMIT,
    append_live_event,
    append_lobby_event_to_file,
    clean_lobby_text,
    read_live_events,
    read_live_events_after,
    read_lobby_events_after,
    read_side_chat_events_after,
    write_live_state,
)
from agentsassemble.side_chat import (
    _filter_side_chat_events_for_meeting,
)
from agentsassemble.sse_cadence import SSE_EVENT_POLL_INTERVAL_SECONDS, SSE_KEEPALIVE_INTERVAL_SECONDS

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
LIVE_AGENT_TURN_LOCK = threading.Lock()
LIVE_AGENT_LOBBY_LOCK = threading.RLock()
REAL_SESSION_SMOKE_REDACTED_SOURCE_EVENT_IDS: set[str] = set()
MAX_LIVE_AGENT_SEQUENCE_TURNS = 12
MAX_LIVE_AGENT_ROUND_BATCH = 8
LIVE_AGENT_ROUND_SCHEDULER_LOCKS: dict[str, threading.RLock] = {}
LIVE_AGENT_ROUND_SCHEDULER_LOCKS_LOCK = threading.Lock()
SESSION_RUN_MONITOR_ERROR = "Live-agent session run monitor failed."
SESSION_ENSURE_REASON_RESIDENT_SESSION_ID_DRIFT = "resident_session_id_drift"
SESSION_ENSURE_REASON_STALE_LOBBY_OBSERVATION = "stale_lobby_observation"
SESSION_ENSURE_REASON_STALE_LIVE_OBSERVATION = "stale_live_observation"
SESSION_ENSURE_REASONS = {
    SESSION_ENSURE_REASON_RESIDENT_SESSION_ID_DRIFT,
    SESSION_ENSURE_REASON_STALE_LOBBY_OBSERVATION,
    SESSION_ENSURE_REASON_STALE_LIVE_OBSERVATION,
}


def _live_agent_round_scheduler_lock(meeting_id: str) -> threading.RLock:
    with LIVE_AGENT_ROUND_SCHEDULER_LOCKS_LOCK:
        lock = LIVE_AGENT_ROUND_SCHEDULER_LOCKS.get(meeting_id)
        if lock is None:
            lock = threading.RLock()
            LIVE_AGENT_ROUND_SCHEDULER_LOCKS[meeting_id] = lock
        return lock


def _backfill_room_registry(output_root: Path) -> None:
    """Register pre-existing meeting dirs into the rooms table.

    The rooms registry only fills going forward (on ensure), so rooms created
    before it existed — or before a localStorage clear — wouldn't show in
    /api/rooms. Seed them once from the meeting dirs on disk so old rooms
    resurface in the dock. Idempotent (skips known rooms) and best-effort: a
    failure here must never block server startup.
    """
    try:
        known = {str(room.get("room_id")) for room in list_rooms(include_archived=True)}
        owner = operator_user_id()
        for meeting in list_meetings(output_root):
            meeting_id = str(meeting.get("meeting_id") or "")
            if not meeting_id or meeting_id in known:
                continue
            upsert_room(
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
    attention_shadow_mode: str = "off",
) -> GuiApplicationServices:
    """Build one ownership graph for the GUI server and handler factory."""

    if room_realtime_controller_override is not None:
        room_repository = room_repository_override or room_realtime_controller_override.store
        if room_repository is not room_realtime_controller_override.store:
            raise ValueError(
                "Room realtime controller and GUI routes must share one room repository instance."
            )
        owns_room_repository = bool(owns_room_repository_override and room_repository_override is not None)
    else:
        room_repository = room_repository_override or RoomStore(output_root)
        owns_room_repository = bool(room_repository_override is None or owns_room_repository_override)

    cleanup_actions: list[tuple[str, Callable[[], object]]] = []

    def remember_cleanup(name: str, callback: Callable[[], object]) -> None:
        cleanup_actions.append((name, callback))

    if owns_room_repository:
        remember_cleanup("room_repository.close", room_repository.close)

    owns_process_supervisor = process_supervisor is None
    owns_session_run_monitor = session_run_monitor is None
    owns_public_tunnel_manager = public_tunnel_manager is None
    owns_room_realtime_controller = room_realtime_controller_override is None

    try:
        configure_room_invite_store(default_room_invite_store_path(output_root))
        configure_room_users_store(default_identity_db_path(output_root))
        _backfill_room_registry(output_root)
        identity_backend = identity_store_for_output_root(output_root)

        live_agent_process_supervisor = process_supervisor or LiveAgentProcessSupervisor(output_root)
        if owns_process_supervisor:
            remember_cleanup("process_supervisor.close", live_agent_process_supervisor.close)

        live_agent_session_run_controller = session_run_controller or LiveAgentSessionRunController(output_root)
        live_agent_flow_supervisor = flow_supervisor or LiveAgentFlowSupervisor(output_root)
        invite_tunnel_manager = public_tunnel_manager or PublicTunnelManager()
        if owns_public_tunnel_manager:
            remember_cleanup("public_tunnel_manager.stop", invite_tunnel_manager.stop)

        live_agent_session_run_monitor = session_run_monitor or LiveAgentSessionRunMonitor(
            output_root,
            live_agent_process_supervisor,
            live_agent_session_run_controller,
            default_server="",
        )
        if owns_session_run_monitor:
            remember_cleanup("session_run_monitor.stop", live_agent_session_run_monitor.stop)

        ws_ticket_store = WsTicketStore()
        native_cli_bridge_manager: NativeCliBridgeProcessManager | None = None
        if room_realtime_controller_override is not None:
            room_realtime_controller = room_realtime_controller_override
        else:
            native_cli_bridge_manager = NativeCliBridgeProcessManager(output_root)
            built_controller: RoomRealtimeController | None = None
            try:
                built_controller = RoomRealtimeController(
                    output_root,
                    providers=default_native_cli_provider_specs(workspace=Path.cwd()),
                    bridge_manager=native_cli_bridge_manager,
                    repository=room_repository,
                    attention_shadow_mode=attention_shadow_mode,
                )
                native_cli_bridge_manager.set_exit_listener(built_controller.bridge_process_exited)
            except BaseException as error:
                try:
                    if built_controller is not None:
                        built_controller.close()
                    else:
                        native_cli_bridge_manager.close()
                except BaseException as cleanup_error:
                    error.add_note(
                        "GUI realtime construction cleanup failed: "
                        f"{cleanup_error}"
                    )
                raise
            room_realtime_controller = built_controller
            remember_cleanup("room_realtime_controller.close", room_realtime_controller.close)

        services = GuiApplicationServices(
            output_root=output_root,
            room_repository=room_repository,
            identity_backend=identity_backend,
            invite_store_path=default_room_invite_store_path(output_root),
            media_store=FileAttachmentStore(output_root),
            process_supervisor=live_agent_process_supervisor,
            session_run_controller=live_agent_session_run_controller,
            session_run_monitor=live_agent_session_run_monitor,
            flow_supervisor=live_agent_flow_supervisor,
            public_tunnel_manager=invite_tunnel_manager,
            ws_ticket_store=ws_ticket_store,
            native_cli_bridge_manager=native_cli_bridge_manager,
            room_realtime_controller=room_realtime_controller,
            owns_room_repository=owns_room_repository,
            owns_process_supervisor=owns_process_supervisor,
            owns_session_run_monitor=owns_session_run_monitor,
            owns_public_tunnel_manager=owns_public_tunnel_manager,
            owns_room_realtime_controller=owns_room_realtime_controller,
        )
    except BaseException as error:
        for name, callback in reversed(cleanup_actions):
            try:
                callback()
            except BaseException as cleanup_error:
                error.add_note(f"GUI service construction cleanup failed in {name}: {cleanup_error}")
        raise
    cleanup_actions.clear()
    return services


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
    if not _is_loopback_host(host) and not unsafe_expose_control_plane:
        raise ValueError(
            "Direct non-loopback GUI bind is disabled because it exposes the local control plane. "
            "Use a loopback bind with the public tunnel, or pass --unsafe-expose-control-plane "
            "only on an isolated trusted network."
        )
    root = output_root or Path(".agentsassemble")
    room_repository_settings = RoomRepositorySettings.from_environment(
        backend=room_repository_backend,
        postgres_dsn_env=room_postgres_dsn_env,
    )
    room_repository = build_room_repository(root, room_repository_settings)
    services: GuiApplicationServices | None = None
    server: ThreadingHTTPServer | None = None
    try:
        services = _build_gui_application_services(
            root,
            room_repository_override=room_repository,
            owns_room_repository_override=True,
            attention_shadow_mode=attention_shadow_mode,
        )
        handler = _make_handler(
            root,
            application_services=services,
            process_supervisor=services.process_supervisor,
            session_run_controller=services.session_run_controller,
            session_run_monitor=services.session_run_monitor,
            flow_supervisor=services.flow_supervisor,
            frontend_dist_root=frontend_dist_root,
            public_tunnel_manager=services.public_tunnel_manager,
            room_repository_override=room_repository,
            attention_shadow_mode=attention_shadow_mode,
        )
        server = ThreadingHTTPServer((host, port), handler)
    except BaseException as error:
        if services is not None:
            try:
                services.close()
            except BaseException as cleanup_error:
                error.add_note(f"GUI service cleanup after startup failure failed: {cleanup_error}")
        raise
    if not _is_loopback_host(host):
        print(
            f"WARNING: AgentsAssemble GUI explicitly bound to non-loopback host {host!r}; the control "
            "plane is unauthenticated and can launch local processes. This unsafe mode is for isolated networks only."
        )
    try:
        if host_token:
            set_runtime_host_token(host_token)
        if public_url:
            set_runtime_public_url(public_url)
        if (public_url or start_public_tunnel) and not get_host_token():
            generated_token = generate_runtime_host_token()
            print(f"AgentsAssemble host token: {generated_token}")
        assert services is not None
        assert server is not None
        server_url = _local_server_url(server.server_address)

        def autostart(server: str) -> None:
            if live_agent_config is None:
                return
            _autostart_live_agent_group(
                root,
                services.process_supervisor,
                config_path=live_agent_config,
                server_url=server,
                group_id=live_agent_group_id,
                auto_restart=live_agent_auto_restart,
                max_restarts=live_agent_max_restarts,
                restart_backoff_seconds=live_agent_restart_backoff_seconds,
                stale_restart_after_seconds=live_agent_stale_restart_after_seconds,
            )

        services.start(
            server_url,
            before_session_monitor=autostart,
            start_public_tunnel=start_public_tunnel,
        )
        _print_gui_startup_banner(
            server_url,
            frontend_dist_root=frontend_dist_root,
            room_repository_backend=room_repository_settings.backend,
        )
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AgentsAssemble GUI")
    finally:
        assert services is not None
        assert server is not None
        services.shutdown(transport_close=server.server_close)


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
            touch_room(room_id)
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
    agent_meeting_id = clean_lobby_text(agent.get("meeting_id"), limit=128)
    identity = ActorIdentity(
        agent_id=actor_id,
        display_name=str(agent.get("display_name") or agent.get("agent_id") or agent_id),
        participant_type="live_session",
        meeting_id=agent_meeting_id,
    )
    try:
        ensure_lobby_say_allowed(output_root, identity, is_muted=is_room_member_muted)
    except GovernedLobbySayRejected as rejected:
        if rejected.category == "muted":
            raise ValueError("This participant is muted by the room host.") from rejected
        raise ValueError(str(rejected)) from rejected
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
        try:
            event = governed_lobby_say(
                output_root,
                identity=identity,
                payload={
                    "kind": payload.get("kind") or "message",
                    "message": _real_session_smoke_reply_message(source_event_id, message),
                    "source_event_id": source_event_id,
                    "auto_chain_depth": payload.get("auto_chain_depth") or 0,
                    **flow_metadata,
                },
                append_lobby_event=append_lobby_event,
                public_lobby_allows_room_scope=_public_lobby_allows_room_scope,
                is_muted=is_room_member_muted,
                policy_already_checked=True,
                side="other-agent",
                live_agent_endpoint=True,
                allow_flow_metadata=True,
                allowed_kinds=LOBBY_KINDS,
            )
        except GovernedLobbySayRejected as rejected:
            raise ValueError(str(rejected)) from rejected
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
            review_checkpoint_id = clean_lobby_text(request_event.get("review_checkpoint_id"), limit=128)
            event = governed_official_reply(
                meeting_dir,
                identity=ActorIdentity(
                    agent_id=agent_id,
                    display_name=display_name,
                    participant_type="live_session",
                    meeting_id=meeting_id,
                ),
                meeting_id=meeting_id,
                source_event_id=source_event_id,
                role_id=role_id,
                display_name=display_name,
                content=content,
                turn_id=clean_lobby_text(request_event.get("turn_id"), limit=128),
                turn_index=turn_index,
                review_checkpoint_id=review_checkpoint_id,
                append_live_event=append_live_event,
            )
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


def _operation_agent_engagement(output_root: Path, agent_id: str) -> str:
    for agent in read_live_agents(output_root):
        if str(agent.get("agent_id") or "") == agent_id:
            return str(agent.get("engagement_mode") or "")
    return ""


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
    attention_shadow_mode: str = "off",
    legacy_session_run_actions_override: LegacySessionRunActions | None = None,
) -> type[BaseHTTPRequestHandler]:
    react_app_root = (frontend_dist_root or default_frontend_dist_root()).resolve()
    services = application_services or _build_gui_application_services(
        output_root,
        process_supervisor=process_supervisor,
        session_run_controller=session_run_controller,
        session_run_monitor=session_run_monitor,
        flow_supervisor=flow_supervisor,
        public_tunnel_manager=public_tunnel_manager,
        room_realtime_controller_override=room_realtime_controller_override,
        room_repository_override=room_repository_override,
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

    live_agent_process_supervisor = services.process_supervisor
    live_agent_session_run_controller = services.session_run_controller
    live_agent_flow_supervisor = services.flow_supervisor
    invite_tunnel_manager = services.public_tunnel_manager
    # Single-use tickets bind a verified session to a /ws open. Browsers cannot
    # set Authorization on ``new WebSocket``.
    ws_ticket_store = services.ws_ticket_store
    room_realtime_controller = services.room_realtime_controller
    room_repository = services.room_repository

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
                    append_lobby_event=append_lobby_event,
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
                from agentsassemble.ws_room_session import WsCommandRejected

                raise WsCommandRejected(str(rejected), code=rejected.code) from rejected

        return WsRoomDeps(
            read_lobby_after=read_lobby_after,
            read_roster=read_roster,
            read_side_chat_after=read_side_chat_after,
            post_say=post_say,
            is_muted=lambda meeting_id, agent_id: is_room_member_muted(output_root, meeting_id, agent_id),
            set_thinking=set_thinking,
            is_session_active=lambda session_token: bool(verify_session_token(session_token)),
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
        attachment_store=services.media_store,
        process_supervisor=live_agent_process_supervisor,
        read_lobby=read_lobby,
        read_lobby_before=read_lobby_before,
        append_lobby_event=append_lobby_event,
        lobby_payload_with_attachments=lobby_payload_with_attachments,
        public_lobby_allows_room_scope=_public_lobby_allows_room_scope,
        history_page_limit=_history_page_limit,
    )
    route_table = Router()
    register_ws_ticket_route(
        route_table,
        ws_ticket_store=ws_ticket_store,
        is_local_operator=lambda ctx: ctx.handler._request_is_local_operator(),
    )
    register_attachment_routes(route_table)
    register_room_routes(route_table)
    register_room_settings_routes(route_table)
    register_side_chat_routes(route_table)
    register_legacy_meeting_routes(
        route_table,
        queries=LegacyMeetingQueryService(output_root),
    )

    def _enqueue_legacy_lobby_auto_turn(event: dict[str, object]) -> None:
        enqueue_agent_session_auto_turn_for_lobby_event(
            output_root,
            event,
            turn_adapter=_local_agent_session_turn_adapter,
            repository=room_repository,
        )

    register_legacy_lobby_routes(
        route_table,
        commands=LegacyLobbyCommandService(
            output_root=output_root,
            append_lobby_event=append_lobby_event,
            public_lobby_allows_room_scope=_public_lobby_allows_room_scope,
            is_muted=is_room_member_muted,
            requester=lambda: REMOTE_LOBBY_REQUESTER,
        ),
        enqueue_auto_turn=_enqueue_legacy_lobby_auto_turn,
    )

    def _late_room_friend_direct_dm(ctx: RequestContext, payload: dict[str, object]) -> dict[str, object]:
        return room_friend_direct_dm_payload(
            ctx.deps.output_root,
            ctx.deps.process_supervisor,
            payload,
            default_server=ctx.handler._request_server_url(),
        )

    register_room_friend_profile_routes(route_table, post_direct_dm=_late_room_friend_direct_dm)

    def _late_provider_credentials_allowed(ctx: RequestContext) -> bool:
        return ctx.handler._provider_credentials_allowed()

    register_provider_routes(
        route_table,
        credentials_allowed=_late_provider_credentials_allowed,
        is_local_operator=lambda ctx: ctx.handler._request_is_local_operator(),
        login_service=ProviderLoginService(
            output_root=output_root,
            command_launcher=live_agent_login_launcher,
            command_resolver=live_agent_login_command_resolver,
        ),
    )

    register_public_invite_admin_routes(
        route_table,
        tunnel=invite_tunnel_manager,
        is_local_operator=lambda ctx: ctx.handler._request_is_local_operator(),
        local_server_url=lambda ctx: ctx.handler._local_server_url(),
    )

    register_live_agent_flow_routes(
        route_table,
        flow=live_agent_flow_supervisor,
        is_loopback_request=lambda ctx: ctx.handler._request_uses_loopback_host(),
        read_operation_payload=lambda ctx, operation: ctx.handler._operation_json_payload(
            operation=operation,
            target_id="",
        ),
        record_operation=record_live_agent_operation,
    )

    register_observability_routes(route_table, processes=live_agent_process_supervisor)
    legacy_health_queries = LegacyLiveAgentHealthQueryService(
        output_root=output_root,
        processes=live_agent_process_supervisor,
        session_run_monitor=session_run_monitor,
    )
    register_legacy_live_agent_read_routes(
        route_table,
        deps=LegacyLiveAgentReadDeps(
            queries=LegacyLiveAgentQueryService.build(output_root),
            roster=LegacyLiveAgentRosterQueryService(output_root),
            health=legacy_health_queries,
            diagnostics=LegacyLiveAgentDiagnosticQueryService(
                output_root=output_root,
                processes=live_agent_process_supervisor,
                session_run_controller=live_agent_session_run_controller,
            ),
            readiness_error_message=_session_check_error_message,
        ),
    )

    def _late_operation_json_payload(
        ctx: RequestContext,
        operation_name: str,
        target_id: str = "",
    ) -> dict[str, object] | None:
        return ctx.handler._operation_json_payload(operation=operation_name, target_id=target_id)

    register_legacy_live_agent_preflight_route(
        route_table,
        deps=LegacyLiveAgentPreflightHttpDeps(
            preflight=LegacyLiveAgentPreflightService(),
            read_operation_payload=_late_operation_json_payload,
            record_operation=record_live_agent_operation,
            request_server_url=lambda ctx: ctx.handler._request_server_url(),
        ),
    )
    register_legacy_live_agent_discovery_route(
        route_table,
        deps=LegacyLiveAgentDiscoveryHttpDeps(
            discovery=LegacyLiveAgentDiscoveryService(output_root),
            read_operation_payload=_late_operation_json_payload,
            record_operation=record_live_agent_operation,
            request_server_url=lambda ctx: ctx.handler._request_server_url(),
        ),
    )
    legacy_smoke_service = LegacyLiveAgentSmokeService(
        output_root,
        basic_smoke_runner=lambda **kwargs: run_live_agent_smoke(**kwargs),
        official_round_smoke_runner=lambda **kwargs: run_live_agent_official_round_smoke(**kwargs),
        session_smoke_runner=lambda **kwargs: run_live_agent_session_smoke(**kwargs),
        real_session_smoke_runner=lambda **kwargs: run_live_agent_real_session_smoke(**kwargs),
    )
    register_legacy_live_agent_smoke_routes(
        route_table,
        deps=LegacyLiveAgentSmokeHttpDeps(
            smoke=legacy_smoke_service,
            read_operation_payload=_late_operation_json_payload,
            record_operation=record_live_agent_operation,
            local_server_url=lambda ctx: ctx.handler._local_server_url(),
        ),
    )
    register_legacy_live_agent_readiness_route(
        route_table,
        deps=LegacyLiveAgentReadinessHttpDeps(
            readiness=LegacyLiveAgentReadinessService(
                output_root=output_root,
                processes=live_agent_process_supervisor,
                health=legacy_health_queries,
                smoke=legacy_smoke_service,
                probe_runner=lambda *args, **kwargs: run_live_agent_probe(*args, **kwargs),
            ),
            read_operation_payload=_late_operation_json_payload,
            record_operation=record_live_agent_operation,
            local_server_url=lambda ctx: ctx.handler._local_server_url(),
        ),
    )

    legacy_session_service = LegacyLiveAgentSessionMutationService(
        output_root,
        processes=live_agent_process_supervisor,
        session_runs=live_agent_session_run_controller,
        actions=LegacySessionMutationActions(
            start=live_agent_session_start_payload,
            ensure=live_agent_session_ensure_payload,
            resume=live_agent_session_resume_payload,
            resume_agent=live_agent_session_resume_agent_payload,
            agent_timing=live_agent_session_agent_timing_payload,
            agent_options=live_agent_session_agent_options_payload,
            check=live_agent_session_check_payload,
            restart=live_agent_session_restart_payload,
            recover=live_agent_session_recover_payload,
            stop=live_agent_session_stop_payload,
            stop_agent=live_agent_session_stop_agent_payload,
        ),
        record_operation=record_live_agent_operation,
    )
    register_legacy_session_mutation_routes(
        route_table,
        deps=LegacySessionHttpDeps(
            service=legacy_session_service,
            read_operation_payload=_late_operation_json_payload,
            default_server_url=lambda ctx: ctx.handler._request_server_url(),
        ),
    )

    legacy_process_service = LegacyLiveAgentProcessMutationService(
        output_root,
        processes=live_agent_process_supervisor,
        actions=LegacyProcessMutationActions(
            start=start_live_agent_process_payload,
            stop_running=stop_running_live_agent_processes_payload,
            stop=stop_live_agent_process_payload,
            restart=restart_live_agent_process_payload,
            recover=recover_live_agent_process_payload,
        ),
        record_operation=record_live_agent_operation,
    )
    register_legacy_process_mutation_routes(
        route_table,
        deps=LegacyProcessHttpDeps(
            service=legacy_process_service,
            read_operation_payload=_late_operation_json_payload,
            default_server_url=lambda ctx: ctx.handler._request_server_url(),
        ),
    )
    register_legacy_session_run_basic_routes(
        route_table,
        deps=LegacySessionRunHttpDeps(
            service=LegacyLiveAgentSessionRunMutationService(
                output_root,
                session_runs=live_agent_session_run_controller,
                actions=legacy_session_run_actions_override
                or LegacySessionRunActions(
                    should_reconcile=lambda run, *, target_run_id: _session_run_monitor_should_reconcile(
                        output_root,
                        live_agent_process_supervisor,
                        run,
                        target_run_id=target_run_id,
                    ),
                    reconcile=lambda *, default_server, target_run_id, approve_real_providers: (
                        _reconcile_live_agent_session_runs(
                            output_root,
                            live_agent_process_supervisor,
                            live_agent_session_run_controller,
                            default_server=default_server,
                            summary="retried durable live-agent session run immediately",
                            target_run_id=target_run_id,
                            request_overrides={"approve_real_providers": approve_real_providers},
                        )
                    ),
                    assert_launch_approved=lambda payload, *, default_server: _assert_session_run_launch_approved(
                        live_agent_process_supervisor,
                        payload,
                        default_server,
                    ),
                    ensure=lambda payload, *, default_server: live_agent_session_ensure_payload(
                        output_root,
                        live_agent_process_supervisor,
                        payload,
                        default_server=default_server,
                    ),
                ),
                record_operation=record_live_agent_operation,
            ),
            read_operation_payload=_late_operation_json_payload,
            default_server_url=lambda ctx: ctx.handler._request_server_url(),
        ),
    )
    register_legacy_self_managed_agent_routes(
        route_table,
        service=LegacySelfManagedAgentService(output_root),
    )

    register_mafia_routes(route_table, read_operation_payload=_late_operation_json_payload)

    class AgentsAssembleHandler(GuiResponseMethods, BaseHTTPRequestHandler):
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
            if path == "/api/live-agent-create/options":
                self._send_json(frontend_live_agent_options_payload(default_workspace=Path.cwd()))
                return
            if path == "/api/provider-sessions":
                # Local sessions for a provider so the create flow can resume one.
                provider_kind = (query.get("provider_kind") or query.get("provider") or [""])[0]
                workspace = (query.get("workspace") or query.get("workspace_path") or [""])[0]
                self._send_json(
                    {
                        "sessions": list_provider_sessions(
                            clean_lobby_text(provider_kind, limit=64),
                            workspace=clean_lobby_text(workspace, limit=512),
                        )
                    }
                )
                return
            if path == "/api/codex-sessions":
                self._send_json(codex_sessions_payload(limit=self._limit(query, default=20)))
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
            if parsed.path == "/api/demo":
                result = run_demo_meeting(adapter_name="mock", output_root=output_root)
                self._send_json({"meeting_id": result.meeting_id, "path": str(result.meeting_dir)})
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
            if route_table.dispatch("DELETE", RequestContext(self, route_deps, parsed, query)):
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

        def _provider_credentials_allowed(self) -> bool:
            ctx = RequestContext(self, route_deps, urlparse(self.path), parse_qs(urlparse(self.path).query))
            if not self._request_is_local_operator() and not ctx.require_moderator():
                return False
            if self._request_uses_loopback_host():
                return True
            forwarded = str(self.headers.get("X-Forwarded-Proto") or "").lower()
            public_scheme = urlparse(get_public_url()).scheme.lower()
            if forwarded != "https" and public_scheme != "https":
                self._send_error(HTTPStatus.FORBIDDEN, "HTTPS is required for remote credential management")
                return False
            return True

        def _local_server_url(self) -> str:
            return _local_server_url(self.server.server_address)

        def _handle_ws_upgrade(self, query: dict) -> None:
            """Upgrade the one authenticated room socket used by browsers and bridges."""
            return handle_ws_upgrade(
                self,
                query,
                ws_ticket_store=ws_ticket_store,
                room_realtime_controller=room_realtime_controller,
                ws_room_deps_factory=_ws_room_deps,
            )

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
                        repository=room_repository,
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

        def _send_room_events_sse_stream(self, *, room_id: str, cursor: str | None = None) -> None:
            self.close_connection = True
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self._send_public_invite_cors_headers()
            self.end_headers()
            current_cursor = cursor or ""
            last_write_at = 0.0
            while True:
                try:
                    frames = room_sse_frames_after_cursor(
                        output_root,
                        room_id,
                        cursor=current_cursor,
                        repository=room_repository,
                    )
                    for frame in frames:
                        self.wfile.write(frame.encode("utf-8"))
                        event_id = _sse_frame_id(frame)
                        if event_id:
                            current_cursor = event_id
                    self.wfile.flush()
                    last_write_at = time.monotonic()
                    time.sleep(SSE_EVENT_POLL_INTERVAL_SECONDS)
                    if time.monotonic() - last_write_at >= SSE_KEEPALIVE_INTERVAL_SECONDS:
                        self.wfile.write(b"event: heartbeat\ndata: {}\n\n")
                        self.wfile.flush()
                        last_write_at = time.monotonic()
                except (ValueError, FileNotFoundError) as error:
                    try:
                        self.wfile.write(_sse_event("error", _sse_stream_error_payload("room_events", error, meeting_id=room_id)))
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
            code: str = "",
            details: dict[str, object] | None = None,
        ) -> None:
            payload: dict[str, object] = {"error": message}
            if code:
                payload["code"] = code
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

        def _limit(self, query: dict[str, list[str]], default: int) -> int:
            try:
                return int(query.get("limit", [str(default)])[0])
            except (TypeError, ValueError):
                return default

    AgentsAssembleHandler.application_services = services
    AgentsAssembleHandler.room_realtime_controller = room_realtime_controller
    AgentsAssembleHandler.room_repository = room_repository
    AgentsAssembleHandler.gui_deps = route_deps
    return AgentsAssembleHandler


def _sse_frame_id(frame: str) -> str:
    for line in frame.splitlines():
        if line.startswith("id:"):
            return line.removeprefix("id:").strip()
    return ""


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
