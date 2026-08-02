from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
from collections.abc import Callable
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble.legacy.live_agent.codex_sessions import list_codex_sessions
from agentsassemble.features.mafia.routes import register_mafia_routes
from agentsassemble.features.side_chat.routes import register_side_chat_routes
from agentsassemble.features.social.routes import (
    register_room_friend_profile_routes,
)
from agentsassemble.legacy.live_agent.runtime.context import live_agent_context_contract
from agentsassemble.legacy.admission_projection import LiveAgentLegacyAdmissionProjection
from agentsassemble.web.routes.providers import (
    model_catalog_payload,
    provider_catalog_payload,
    register_provider_routes,
)
from agentsassemble.web.routes.gui import register_current_gui_routes
from agentsassemble.web.http_server import (
    AgentsAssembleHTTPServer as ThreadingHTTPServer,
)
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
from agentsassemble.legacy.gui_autostart import (
    autostart_live_agent_group as _owned_autostart_live_agent_group,
)
from agentsassemble.legacy.gui_flow import (
    LegacyGuiFlowSupervisor as _OwnedLegacyGuiFlowSupervisor,
    flow_events_for_state as _owned_flow_events_for_state,
    latest_flow_activity_at as _owned_latest_flow_activity_at,
    latest_flow_context as _owned_latest_flow_context,
    parse_iso_datetime as _owned_parse_iso_datetime,
    restored_flow_state as _owned_restored_flow_state,
    safe_live_agent_flow_agents as _owned_safe_live_agent_flow_agents,
)
from agentsassemble.legacy.gui_lobby import (
    LOBBY_APPEND_LOCK as _OWNED_LOBBY_APPEND_LOCK,
    append_lobby_event as _owned_append_lobby_event,
    lobby_payload_with_attachments as _owned_lobby_payload_with_attachments,
    public_lobby_allows_room_scope as _owned_public_lobby_allows_room_scope,
    single_lobby_meeting_id as _owned_single_lobby_meeting_id,
)
from agentsassemble.legacy.gui_payload import (
    as_dict_list as _owned_as_dict_list,
    index_by_id as _owned_index_by_id,
    operation_group_id as _owned_operation_group_id,
    operation_group_ids as _owned_operation_group_ids,
    operation_result_status as _owned_operation_result_status,
    operation_success_for_result as _owned_operation_success_for_result,
    optional_str as _owned_optional_str,
    payload_bool as _owned_payload_bool,
    payload_nonnegative_float as _owned_payload_nonnegative_float,
    payload_nonnegative_int as _owned_payload_nonnegative_int,
    payload_optional_int as _owned_payload_optional_int,
    safe_payload_strings as _owned_safe_payload_strings,
)
from agentsassemble.legacy.gui_session_readiness import (
    SESSION_ENSURE_REASON_RESIDENT_SESSION_ID_DRIFT,
    SESSION_ENSURE_REASON_STALE_LIVE_OBSERVATION,
    SESSION_ENSURE_REASON_STALE_LOBBY_OBSERVATION,
    ensured_readiness_payload as _owned_ensured_readiness_payload,
    event_is_stale_for_observation_restart as _owned_event_is_stale_for_observation_restart,
    find_session_process_group as _owned_find_session_process_group,
    observation_restart_stale_after_seconds as _owned_observation_restart_stale_after_seconds,
    optional_readiness_payload as _owned_optional_readiness_payload,
    process_group_uses_requested_config as _owned_process_group_uses_requested_config,
    ready_session_has_stale_live_observation_lag as _owned_ready_session_has_stale_live_observation_lag,
    ready_session_has_stale_lobby_observation_lag as _owned_ready_session_has_stale_lobby_observation_lag,
    ready_session_requires_restart_for_resident_session_drift as _owned_ready_session_requires_restart_for_resident_session_drift,
    ready_session_requires_restart_for_stale_observation_lag as _owned_ready_session_requires_restart_for_stale_observation_lag,
    resident_session_ids_by_agent as _owned_resident_session_ids_by_agent,
    safe_process_group_meeting_id as _owned_safe_process_group_meeting_id,
    session_payload_with_group_owner as _owned_session_payload_with_group_owner,
    stale_observation_restart_count as _owned_stale_observation_restart_count,
    stale_observation_restart_decision as _owned_stale_observation_restart_decision,
)
from agentsassemble.legacy.gui_session_lifecycle import (
    ensure_session_payload as _owned_ensure_session_payload,
    recover_session_payload as _owned_recover_session_payload,
    restart_session_payload as _owned_restart_session_payload,
    resume_session_agent_payload as _owned_resume_session_agent_payload,
    resume_session_payload as _owned_resume_session_payload,
    start_session_payload as _owned_start_session_payload,
    stop_session_agent_payload as _owned_stop_session_agent_payload,
    stop_session_payload as _owned_stop_session_payload,
)
from agentsassemble.legacy.gui_session_settings import (
    agent_options_payload as _owned_agent_options_payload,
    agent_timing_payload as _owned_agent_timing_payload,
)
from agentsassemble.legacy.gui_session_rounds import (
    attach_session_auto_rounds_if_requested as _owned_attach_session_auto_rounds_if_requested,
    session_auto_rounds_options as _owned_session_auto_rounds_options,
    skipped_session_auto_rounds_result as _owned_skipped_session_auto_rounds_result,
)
from agentsassemble.legacy.gui_session_probes import (
    REAL_SESSION_SMOKE_PROBE_REDACTION,
    REAL_SESSION_SMOKE_REPLY_REDACTION,
    REDACTED_SOURCE_EVENT_IDS as REAL_SESSION_SMOKE_REDACTED_SOURCE_EVENT_IDS,
    live_agent_engagement_snapshot as _owned_live_agent_engagement_snapshot,
    read_live_agent_presence_state as _owned_read_live_agent_presence_state,
    real_session_smoke_reply_message as _owned_real_session_smoke_reply_message,
    redact_real_session_smoke_lobby_events as _owned_redact_real_session_smoke_lobby_events,
    restore_live_agent_engagement_snapshot as _owned_restore_live_agent_engagement_snapshot,
    run_session_bound_agent_probe as _owned_run_session_bound_agent_probe,
    session_bound_agent_ids as _owned_session_bound_agent_ids,
    session_bound_agent_reply_probe_payload as _owned_session_bound_agent_reply_probe_payload,
    session_reply_probe_summary as _owned_session_reply_probe_summary,
    write_live_agent_presence_state as _owned_write_live_agent_presence_state,
)
from agentsassemble.legacy.gui_processes import (
    record_operation as _owned_record_operation,
    recover_process_payload as _owned_recover_process_payload,
    restart_process_payload as _owned_restart_process_payload,
    start_process_payload as _owned_start_process_payload,
    stop_process_payload as _owned_stop_process_payload,
    stop_running_processes_payload as _owned_stop_running_processes_payload,
)
from agentsassemble.legacy.gui_smoke import (
    aggregate_readiness_payload as _owned_aggregate_readiness_payload,
    basic_smoke_payload as _owned_basic_smoke_payload,
    official_round_smoke_payload as _owned_official_round_smoke_payload,
    real_session_smoke_payload as _owned_real_session_smoke_payload,
    session_smoke_payload as _owned_session_smoke_payload,
)
from agentsassemble.legacy.gui_session_runs import (
    LegacyGuiSessionRunMonitor as _OwnedLegacyGuiSessionRunMonitor,
    LegacyGuiSessionRunRuntime,
    assert_session_run_launch_approved as _owned_assert_session_run_launch_approved,
    reconcile_session_runs as _owned_reconcile_session_runs,
    reconcile_session_runs_on_startup as _owned_reconcile_session_runs_on_startup,
    session_run_monitor_should_reconcile as _owned_session_run_monitor_should_reconcile,
    session_run_reconcile_launch_policy_targets as _owned_session_run_reconcile_launch_policy_targets,
    session_run_reconcile_request as _owned_session_run_reconcile_request,
)
from agentsassemble.legacy.http.sse_transport import (
    filter_lobby_events_for_meeting as _owned_filter_lobby_events_for_meeting,
    meeting_not_found_error as _owned_meeting_not_found_error,
    payload_signature as _owned_payload_signature,
    sse_frame_id as _owned_sse_frame_id,
    sse_stream_error_payload as _owned_sse_stream_error_payload,
    stream_snapshot_payload as _owned_stream_snapshot_payload,
)
from agentsassemble.web.routes.observability import register_observability_routes
from agentsassemble.web.routes.public_invite import register_public_invite_admin_routes
from agentsassemble.legacy.meeting.http.room_composition import register_room_routes
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
from agentsassemble.room.realtime import RoomRealtimeController
from agentsassemble.application.session_run_monitor import (
    PeriodicSessionRunMonitor,
    safe_monitor_error_type,
)
from agentsassemble.legacy.live_agent.runtime.join_brief import live_agent_join_brief_payload
from agentsassemble.legacy.live_agent.runtime.launch_policy import APPROVAL_REQUIRED_MESSAGE, assert_resident_launch_approved
from agentsassemble.legacy.live_agent.health import (
    DEFAULT_SESSION_RUN_MONITOR_INTERVAL_SECONDS,
    MIN_SESSION_RUN_MONITOR_INTERVAL_SECONDS,
    safe_process_group_id as _safe_process_group_id,
)
from agentsassemble.legacy.live_agent.health_queries import live_agent_health_payload
from agentsassemble.legacy.live_agent.discovery import (
    discovery_operation_details as _discovery_operation_details,
    live_agent_discovery_payload,
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
from agentsassemble.legacy.live_agent.state import (
    heartbeat_live_agent,
    read_live_agents,
)
from agentsassemble.legacy.live_agent.runtime.processes import (
    LiveAgentProcessSupervisor,
)
from agentsassemble.legacy.live_agent.runtime.probe import run_live_agent_probe
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
)
from agentsassemble.legacy.live_agent.process_projection import (
    live_agent_processes_payload,
)
from agentsassemble.legacy.live_agent.preflight import (
    live_agent_preflight_payload,
)
from agentsassemble.legacy.live_agent.readiness_projection import (
    readiness_health_operation_details as _readiness_health_operation_details,
)
from agentsassemble.legacy.live_agent.smoke import (
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
from agentsassemble.providers.login import ProviderLoginService
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
from agentsassemble.identity.google import GoogleAccountLoginService
from agentsassemble.persistence.local.identity.registry import identity_store_for_output_root
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
from agentsassemble.web.room_ws_composition import (
    RoomWsComposition,
    build_ws_room_deps_factory,
)
from agentsassemble.application.agent_sessions import room_sse_frames_after_cursor
from agentsassemble.admission.compat import configure_compatibility_invite_repository
from agentsassemble.admission.invite import compatibility_public_invite_runtime
from agentsassemble.legacy.meeting.core.events import (
    ROOM_TOPIC_LIMIT,
    append_live_event,
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


def _safe_live_agent_flow_agents(
    output_root: Path,
    *,
    meeting_id: str = "",
    quota_viewer: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    return _owned_safe_live_agent_flow_agents(
        output_root,
        meeting_id=meeting_id,
        quota_viewer=quota_viewer,
    )


class LiveAgentFlowSupervisor(_OwnedLegacyGuiFlowSupervisor):
    def __init__(self, output_root: Path) -> None:
        super().__init__(
            output_root,
            append_lobby_event=lambda *args, **kwargs: append_lobby_event(
                *args,
                **kwargs,
            ),
        )


def _latest_flow_activity_at(events: list[dict[str, object]], *, flow_id: str) -> str:
    return _owned_latest_flow_activity_at(events, flow_id=flow_id)


def _restored_flow_state(
    events: list[dict[str, object]],
    *,
    meeting_id: str = "",
) -> dict[str, object] | None:
    return _owned_restored_flow_state(events, meeting_id=meeting_id)


def _latest_flow_context(
    events: list[dict[str, object]],
    *,
    meeting_id: str = "",
) -> dict[str, object] | None:
    return _owned_latest_flow_context(events, meeting_id=meeting_id)


def _flow_events_for_state(
    events: list[dict[str, object]],
    flow: dict[str, object] | None,
) -> list[dict[str, object]]:
    return _owned_flow_events_for_state(events, flow)


def _parse_iso_datetime(value: object) -> datetime | None:
    return _owned_parse_iso_datetime(value)
LIVE_AGENT_LOBBY_LOCK = _OWNED_LOBBY_APPEND_LOCK
SESSION_RUN_MONITOR_ERROR = "Live-agent session run monitor failed."
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
    reconcile_startup_sessions: bool = True,
) -> GuiApplicationServices:
    """Select concrete GUI runtimes and delegate ownership composition."""

    services = build_gui_application_services(
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
        reconcile_startup_sessions=reconcile_startup_sessions,
    )
    configure_compatibility_invite_repository(services.invite_repository)
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
    _owned_autostart_live_agent_group(
        output_root,
        process_supervisor,
        config_path=config_path,
        server_url=server_url,
        group_id=group_id,
        auto_restart=auto_restart,
        max_restarts=max_restarts,
        restart_backoff_seconds=restart_backoff_seconds,
        stale_restart_after_seconds=stale_restart_after_seconds,
        record_operation=record_live_agent_operation,
    )


def _reconcile_live_agent_session_runs_on_startup(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    session_run_controller: LiveAgentSessionRunController,
    *,
    default_server: str,
) -> list[dict[str, object]]:
    return _owned_reconcile_session_runs_on_startup(
        output_root,
        process_supervisor,
        session_run_controller,
        default_server=default_server,
        runtime=_legacy_gui_session_run_runtime(),
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
    return _owned_reconcile_session_runs(
        output_root,
        process_supervisor,
        session_run_controller,
        default_server=default_server,
        summary=summary,
        target_run_id=target_run_id,
        request_overrides=request_overrides,
        runtime=_legacy_gui_session_run_runtime(),
    )


def _session_run_reconcile_request(run: dict[str, object]) -> dict[str, object]:
    return _owned_session_run_reconcile_request(run)


def _session_run_reconcile_launch_policy_targets(
    process_supervisor: LiveAgentProcessSupervisor,
    request: dict[str, object],
    default_server: str,
) -> list[tuple[object, str]]:
    return _owned_session_run_reconcile_launch_policy_targets(
        process_supervisor,
        request,
        default_server,
    )


def _assert_session_run_launch_approved(
    process_supervisor: LiveAgentProcessSupervisor,
    request: dict[str, object],
    default_server: str,
) -> None:
    _owned_assert_session_run_launch_approved(
        process_supervisor,
        request,
        default_server,
        payload_bool=_payload_bool,
    )


def _session_run_monitor_should_reconcile(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    run: dict[str, object],
    *,
    target_run_id: str = "",
) -> bool:
    return _owned_session_run_monitor_should_reconcile(
        output_root,
        process_supervisor,
        run,
        target_run_id=target_run_id,
        runtime=_legacy_gui_session_run_runtime(),
    )


def _legacy_gui_session_run_runtime() -> LegacyGuiSessionRunRuntime:
    return LegacyGuiSessionRunRuntime(
        ensure_payload=lambda *args, **kwargs: live_agent_session_ensure_payload(
            *args,
            **kwargs,
        ),
        readiness_payload=lambda *args, **kwargs: live_agent_session_readiness_payload(
            *args,
            **kwargs,
        ),
        ready_session_requires_restart=lambda *args, **kwargs: _ready_session_requires_restart_for_stale_observation_lag(
            *args,
            **kwargs,
        ),
        record_operation=lambda *args, **kwargs: record_live_agent_operation(
            *args,
            **kwargs,
        ),
        payload_bool=_payload_bool,
    )


class LiveAgentSessionRunMonitor(_OwnedLegacyGuiSessionRunMonitor):
    def __init__(
        self,
        output_root: Path,
        process_supervisor: LiveAgentProcessSupervisor,
        session_run_controller: LiveAgentSessionRunController,
        *,
        default_server: str,
        interval_seconds: float = DEFAULT_SESSION_RUN_MONITOR_INTERVAL_SECONDS,
    ) -> None:
        super().__init__(
            output_root,
            process_supervisor,
            session_run_controller,
            default_server=default_server,
            runtime=_legacy_gui_session_run_runtime(),
            interval_seconds=interval_seconds,
        )


def _filter_lobby_events_for_meeting(events: list[dict[str, object]], *, meeting_id: str = "") -> list[dict[str, object]]:
    return _owned_filter_lobby_events_for_meeting(events, meeting_id=meeting_id)


def append_lobby_event(
    output_root: Path,
    event: dict[str, object],
    *,
    live_agent_endpoint: bool = False,
    allow_flow_metadata: bool = False,
    identity_backend: IdentityBackend | None = None,
) -> dict[str, object]:
    return _owned_append_lobby_event(
        output_root,
        event,
        live_agent_endpoint=live_agent_endpoint,
        allow_flow_metadata=allow_flow_metadata,
        identity_backend=identity_backend,
        identity_backend_factory=identity_store_for_output_root,
    )


def lobby_payload_with_attachments(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    return _owned_lobby_payload_with_attachments(
        output_root,
        payload,
        list_meetings=list_meetings,
    )


def _single_lobby_meeting_id(output_root: Path) -> str:
    return _owned_single_lobby_meeting_id(
        output_root,
        list_meetings=list_meetings,
    )


def _public_lobby_allows_room_scope(payload: dict[str, object]) -> bool:
    return _owned_public_lobby_allows_room_scope(payload)


def _meeting_not_found_error(meeting_id: str) -> ValueError:
    return _owned_meeting_not_found_error(meeting_id)


def _sse_stream_error_payload(stream: str, error: Exception, meeting_id: str | None = None) -> dict[str, object]:
    return _owned_sse_stream_error_payload(stream, error, meeting_id=meeting_id)


def _stream_snapshot_payload(
    output_root: Path,
    stream: str,
    meeting_id: str | None = None,
    last_event_id: str | None = None,
    *,
    repository: RoomRepository | None = None,
    sessions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return _owned_stream_snapshot_payload(
        output_root,
        stream,
        meeting_id=meeting_id,
        last_event_id=last_event_id,
        repository=repository,
        sessions=sessions,
        build_meeting_stream=lambda *args, **kwargs: build_meeting_stream_payload(
            *args,
            **kwargs,
        ),
    )


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
    return _owned_start_session_payload(
        output_root,
        process_supervisor,
        payload,
        default_server=default_server,
        attach_auto_rounds=_attach_session_auto_rounds_if_requested,
    )


def live_agent_session_resume_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    return _owned_resume_session_payload(
        output_root,
        process_supervisor,
        payload,
        default_server=default_server,
        attach_auto_rounds=_attach_session_auto_rounds_if_requested,
    )


def live_agent_session_resume_agent_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    return _owned_resume_session_agent_payload(
        output_root,
        process_supervisor,
        payload,
        default_server=default_server,
        attach_auto_rounds=_attach_session_auto_rounds_if_requested,
    )


def live_agent_session_ensure_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    return _owned_ensure_session_payload(
        output_root,
        process_supervisor,
        payload,
        default_server=default_server,
        attach_auto_rounds=_attach_session_auto_rounds_if_requested,
    )


def _live_agent_session_payload_with_group_owner(
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
) -> dict[str, object]:
    return _owned_session_payload_with_group_owner(process_supervisor, payload)


def _safe_process_group_meeting_id(value: object) -> str:
    return _owned_safe_process_group_meeting_id(value)


def _ready_session_requires_restart_for_resident_session_drift(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    current: dict[str, object] | None,
    *,
    default_server: str,
) -> bool:
    return _owned_ready_session_requires_restart_for_resident_session_drift(
        output_root,
        process_supervisor,
        payload,
        current,
        default_server=default_server,
    )


def _process_group_uses_requested_config(group: dict[str, object], live_agent_config_path: str) -> bool:
    return _owned_process_group_uses_requested_config(group, live_agent_config_path)


def _resident_session_ids_by_agent(
    live_agent_config_path: str,
    *,
    server: str,
    meeting_id: str,
) -> dict[str, str]:
    return _owned_resident_session_ids_by_agent(
        live_agent_config_path,
        server=server,
        meeting_id=meeting_id,
    )


def _ready_session_requires_restart_for_stale_observation_lag(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    current: dict[str, object] | None,
) -> bool:
    return _owned_ready_session_requires_restart_for_stale_observation_lag(
        output_root,
        process_supervisor,
        payload,
        current,
    )


def _stale_observation_restart_count(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    current: dict[str, object] | None,
) -> int:
    return _owned_stale_observation_restart_count(
        output_root,
        process_supervisor,
        payload,
        current,
    )


def _stale_observation_restart_decision(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    current: dict[str, object] | None,
) -> tuple[int, str]:
    return _owned_stale_observation_restart_decision(
        output_root,
        process_supervisor,
        payload,
        current,
    )


def _observation_restart_stale_after_seconds(group: dict[str, object]) -> float:
    return _owned_observation_restart_stale_after_seconds(group)


def _ready_session_has_stale_lobby_observation_lag(
    output_root: Path,
    agent_ids: list[str],
    agents_by_id: dict[str, dict[str, object]],
    *,
    stale_after_seconds: float,
) -> bool:
    return _owned_ready_session_has_stale_lobby_observation_lag(
        output_root,
        agent_ids,
        agents_by_id,
        stale_after_seconds=stale_after_seconds,
    )


def _ready_session_has_stale_live_observation_lag(
    output_root: Path,
    meeting_id: str,
    agent_ids: list[str],
    agents_by_id: dict[str, dict[str, object]],
    *,
    stale_after_seconds: float,
) -> bool:
    return _owned_ready_session_has_stale_live_observation_lag(
        output_root,
        meeting_id,
        agent_ids,
        agents_by_id,
        stale_after_seconds=stale_after_seconds,
    )


def _event_is_stale_for_observation_restart(event: dict[str, object], stale_after_seconds: float) -> bool:
    return _owned_event_is_stale_for_observation_restart(
        event,
        stale_after_seconds,
    )


def _live_agent_session_optional_readiness_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
) -> dict[str, object] | None:
    return _owned_optional_readiness_payload(
        output_root,
        process_supervisor,
        payload,
        readiness_payload=live_agent_session_readiness_payload,
    )


def _live_agent_session_ensured_readiness_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    session: dict[str, object],
) -> dict[str, object]:
    return _owned_ensured_readiness_payload(
        output_root,
        process_supervisor,
        payload,
        session,
        readiness_payload=live_agent_session_readiness_payload,
    )


def _find_session_process_group(
    groups: list[dict[str, object]],
    group_id: str,
) -> dict[str, object]:
    return _owned_find_session_process_group(groups, group_id)


def live_agent_session_restart_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    restart_count: int | None = None,
) -> dict[str, object]:
    return _owned_restart_session_payload(
        output_root,
        process_supervisor,
        payload,
        restart_count=restart_count,
        attach_auto_rounds=_attach_session_auto_rounds_if_requested,
    )


def live_agent_session_recover_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
) -> dict[str, object]:
    return _owned_recover_session_payload(
        output_root,
        process_supervisor,
        payload,
        attach_auto_rounds=_attach_session_auto_rounds_if_requested,
    )


def live_agent_session_stop_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
) -> dict[str, object]:
    return _owned_stop_session_payload(
        output_root,
        process_supervisor,
        payload,
    )


def live_agent_session_stop_agent_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
) -> dict[str, object]:
    return _owned_stop_session_agent_payload(
        output_root,
        process_supervisor,
        payload,
    )


def live_agent_session_agent_timing_payload(
    output_root: Path,
    payload: dict[str, object],
) -> dict[str, object]:
    return _owned_agent_timing_payload(output_root, payload)


def live_agent_session_agent_options_payload(
    output_root: Path,
    payload: dict[str, object],
) -> dict[str, object]:
    return _owned_agent_options_payload(output_root, payload)


def _session_auto_rounds_options(payload: dict[str, object]) -> dict[str, object]:
    return _owned_session_auto_rounds_options(payload)


def _attach_session_auto_rounds_if_requested(
    output_root: Path,
    session: dict[str, object],
    payload: dict[str, object],
) -> dict[str, object]:
    return _owned_attach_session_auto_rounds_if_requested(
        output_root,
        session,
        payload,
        reply_probe=_session_bound_agent_reply_probe_payload,
        rounds_payload=live_agent_turn_rounds_payload,
        rounds_finalization=_rounds_finalization_result_if_requested,
        skipped_finalization=_skipped_rounds_finalization_result,
    )


def _session_bound_agent_reply_probe_payload(
    output_root: Path,
    session: dict[str, object],
    payload: dict[str, object],
) -> dict[str, object]:
    return _owned_session_bound_agent_reply_probe_payload(
        output_root,
        session,
        payload,
        run_probe=_run_session_bound_agent_probe,
    )


def _session_bound_agent_ids(session: dict[str, object]) -> list[str]:
    return _owned_session_bound_agent_ids(session)


def _run_session_bound_agent_probe(
    output_root: Path,
    agent_id: str,
    *,
    timeout_seconds: float,
    redact_events: bool = False,
) -> dict[str, object]:
    return _owned_run_session_bound_agent_probe(
        output_root,
        agent_id,
        timeout_seconds=timeout_seconds,
        probe_runner=run_live_agent_probe,
        redact_events=redact_events,
    )


def _redact_real_session_smoke_lobby_events(
    output_root: Path,
    source_event_ids: list[str],
) -> dict[str, object]:
    return _owned_redact_real_session_smoke_lobby_events(
        output_root,
        source_event_ids,
    )


def _real_session_smoke_reply_message(source_event_id: str, message: str) -> str:
    return _owned_real_session_smoke_reply_message(source_event_id, message)


def _live_agent_engagement_snapshot(output_root: Path, agent_id: str) -> dict[str, object]:
    return _owned_live_agent_engagement_snapshot(output_root, agent_id)


def _restore_live_agent_engagement_snapshot(
    output_root: Path,
    agent_id: str,
    snapshot: dict[str, object],
) -> None:
    _owned_restore_live_agent_engagement_snapshot(
        output_root,
        agent_id,
        snapshot,
    )


def _read_live_agent_presence_state(output_root: Path) -> dict[str, object]:
    return _owned_read_live_agent_presence_state(output_root)


def _write_live_agent_presence_state(output_root: Path, state: dict[str, object]) -> None:
    _owned_write_live_agent_presence_state(output_root, state)


def _session_reply_probe_summary(
    agent_ids: list[str],
    probes: list[dict[str, object]],
    *,
    timeout_seconds: float,
    status: str,
    reason: str = "",
) -> dict[str, object]:
    return _owned_session_reply_probe_summary(
        agent_ids,
        probes,
        timeout_seconds=timeout_seconds,
        status=status,
        reason=reason,
    )


def _skipped_session_auto_rounds_result(
    session: dict[str, object],
    options: dict[str, object],
    *,
    reason: str = "session_not_ready",
) -> dict[str, object]:
    return _owned_skipped_session_auto_rounds_result(
        session,
        options,
        reason=reason,
    )


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
        "current_ai_session": {
            "prerequisite": (
                "Register `assemble room connector-mcp` once as an MCP server "
                "in the app or interactive CLI."
            ),
            "action": "Call room_join with this exact /join URL.",
            "result": (
                "The current conversation joins directly. No provider process "
                "or replacement model is launched."
            ),
        },
        "inspect_invite": {
            "request": f"POST {base}/api/room-invite/admission",
            "json": {"invite_token": "<the token query parameter from this /join URL>"},
            "side_effects": "none",
        },
        "other_clients": {
            "human_browser": {
                "when": "admission status is profile_required or existing_session",
                "request": f"POST {base}/api/room-invite/join",
                "json": {
                    "invite_token": "<invite token>",
                    "display_name": "<your name in the room>",
                    "participant_type": "human | agent",
                    "device_token": "<one stable random string per client installation>",
                    "owner_display_name": "<for agents: the human you act for>",
                },
            },
            "explicit_managed_agent": {
                "when": "admission status is agent_client_required",
                "request": f"POST {base}/api/room-invite/agent-join",
                "note": (
                    "This starts or attaches the provider named by the invite as a separate "
                    "Agent Session. It does not move the AI session reading this guide into the room."
                ),
            },
        },
        "leave": f"POST {base}/api/room-invite/leave (Authorization: Bearer <session_token>)",
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
            "inspect_invite": f"POST {base}/api/room-invite/admission",
            "join": f"POST {base}/api/room-invite/join",
            "agent_join": f"POST {base}/api/room-invite/agent-join",
            "current_session_mcp": "assemble room connector-mcp",
            "websocket_ticket": f"POST {base}/api/ws-ticket",
            "websocket": f"{base}/ws?ticket=<single-use-ticket>",
            "leave": f"POST {base}/api/room-invite/leave",
            "companion_invite": f"POST {base}/api/room-invite/companion",
            "flow_status": f"GET {base}/api/live-agent-flow",
        },
        "notes": [
            "Send a stable device_token on join to keep one identity across rejoins.",
            "Models use Room Connector tools; the connector privately owns canonical WebSocket details.",
            "An agent-join launches or attaches a separate provider Agent Session; it is not the caller itself.",
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
    return _owned_basic_smoke_payload(
        payload,
        default_server=default_server,
        request_json=_request_json,
        runner=run_live_agent_smoke,
    )


def live_agent_official_round_smoke_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    """Compatibility seam used by aggregate readiness until that route moves."""
    return _owned_official_round_smoke_payload(
        output_root,
        payload,
        default_server=default_server,
        request_json=_request_json,
        runner=run_live_agent_official_round_smoke,
    )


def live_agent_session_smoke_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    default_server: str,
) -> dict[str, object]:
    """Compatibility seam used by aggregate readiness until that route moves."""
    return _owned_session_smoke_payload(
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
    return _owned_real_session_smoke_payload(
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
    return _owned_aggregate_readiness_payload(
        output_root,
        process_supervisor,
        payload,
        default_server=default_server,
        session_run_monitor=session_run_monitor,
        basic_smoke_runner=lambda **kwargs: run_live_agent_smoke(**kwargs),
        official_round_smoke_runner=lambda **kwargs: run_live_agent_official_round_smoke(
            **kwargs,
        ),
        session_smoke_runner=lambda **kwargs: run_live_agent_session_smoke(**kwargs),
        real_session_smoke_runner=lambda **kwargs: run_live_agent_real_session_smoke(
            **kwargs,
        ),
        probe_runner=lambda *args, **kwargs: run_live_agent_probe(*args, **kwargs),
    )


def start_live_agent_process_payload(
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
    output_root: Path | None = None,
) -> dict[str, object]:
    return _owned_start_process_payload(
        process_supervisor,
        payload,
        default_server=default_server,
        output_root=output_root,
    )


def stop_live_agent_process_payload(
    process_supervisor: LiveAgentProcessSupervisor,
    group_id: str,
    *,
    output_root: Path | None = None,
) -> dict[str, object]:
    return _owned_stop_process_payload(
        process_supervisor,
        group_id,
        output_root=output_root,
    )


def stop_running_live_agent_processes_payload(
    process_supervisor: LiveAgentProcessSupervisor,
    *,
    output_root: Path | None = None,
) -> dict[str, object]:
    return _owned_stop_running_processes_payload(
        process_supervisor,
        output_root=output_root,
    )


def restart_live_agent_process_payload(
    process_supervisor: LiveAgentProcessSupervisor,
    group_id: str,
    *,
    output_root: Path | None = None,
) -> dict[str, object]:
    return _owned_restart_process_payload(
        process_supervisor,
        group_id,
        output_root=output_root,
    )


def recover_live_agent_process_payload(
    process_supervisor: LiveAgentProcessSupervisor,
    group_id: str,
    *,
    output_root: Path | None = None,
) -> dict[str, object]:
    return _owned_recover_process_payload(
        process_supervisor,
        group_id,
        output_root=output_root,
    )


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
    return _owned_record_operation(
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
    return _owned_index_by_id(items)


def _as_dict_list(value: object) -> list[dict[str, object]]:
    return _owned_as_dict_list(value)


def _optional_str(value: object) -> str | None:
    return _owned_optional_str(value)


def _operation_group_id(payload: dict[str, object], group: dict[str, object] | None = None) -> str:
    return _owned_operation_group_id(payload, group)


def _operation_group_ids(records: object) -> list[str]:
    return _owned_operation_group_ids(records)


def _operation_result_status(value: object) -> str:
    return _owned_operation_result_status(value)


def _operation_success_for_result(value: object, *, success_values: set[str]) -> str:
    return _owned_operation_success_for_result(value, success_values=success_values)


def _payload_bool(value: object) -> bool:
    return _owned_payload_bool(value)


def _payload_nonnegative_int(value: object, default: int) -> int:
    return _owned_payload_nonnegative_int(value, default)


def _payload_nonnegative_float(value: object, default: float) -> float:
    return _owned_payload_nonnegative_float(value, default)


def _payload_optional_int(value: object) -> int | None:
    return _owned_payload_optional_int(value)


def _safe_payload_strings(value: object, *, limit: int) -> list[str]:
    return _owned_safe_payload_strings(value, limit=limit)


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
    google_account_service_override: GoogleAccountLoginService | None = None,
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

    ws_room_deps_factory = build_ws_room_deps_factory(
        output_root=output_root,
        services=services,
        room_repository=room_repository,
        composition=RoomWsComposition(
            stream_snapshot_payload=lambda *args, **kwargs: _stream_snapshot_payload(
                *args,
                **kwargs,
            ),
            last_payload_event_id=_last_payload_event_id,
            payload_signature=_payload_signature,
            mark_thinking=mark_thinking,
            local_server_url=_local_server_url,
        ),
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
        room_command_handler=lambda identity, command: room_realtime_controller.handle_command(
            identity,
            command,
        ),
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
        append_lobby_event=append_server_lobby_event,
        public_lobby_allows_room_scope=_public_lobby_allows_room_scope,
        is_muted=is_room_member_muted,
        remote_lobby_requester=lambda: REMOTE_LOBBY_REQUESTER,
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
            command_launcher=live_agent_login_launcher,
            command_resolver=live_agent_login_command_resolver,
            operation_recorder=lambda **kwargs: record_live_agent_operation(
                output_root,
                **kwargs,
            ),
            catalog_refresher=lambda: (
                services.room_realtime_controller.provider_catalog.snapshot(
                    refresh=True
                )
            ),
        ),
        google_account_service=(
            google_account_service_override or GoogleAccountLoginService.from_environment()
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
        ws_room_deps_factory=ws_room_deps_factory,
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
    return _owned_sse_frame_id(frame)


def _payload_signature(payload: dict[str, object]) -> str | None:
    return _owned_payload_signature(payload)
