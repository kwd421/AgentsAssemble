"""Owned composition for retained legacy GUI routes.

The current room application injects repositories and services through
``GuiApplicationServices``. Retained meeting/live-agent HTTP compatibility
routes still need older services and patch seams, but their construction does
not belong in the main server transport module. This bundle keeps that
compatibility graph explicit until the routes can be retired.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.agent_sessions import enqueue_agent_session_auto_turn_for_lobby_event
from agentsassemble.application.gui import SessionRunMonitor
from agentsassemble.gui_legacy_codex_session_http import (
    LegacyCodexSessionHttpDeps,
    register_legacy_codex_session_routes,
)
from agentsassemble.gui_legacy_live_agent_discovery_http import (
    LegacyLiveAgentDiscoveryHttpDeps,
    register_legacy_live_agent_discovery_route,
)
from agentsassemble.legacy.live_agent.http.engagement import (
    register_legacy_live_agent_engagement_route,
)
from agentsassemble.legacy.live_agent.http.join_brief import (
    register_legacy_live_agent_join_brief_route,
)
from agentsassemble.legacy.live_agent.http.official_reply import (
    LegacyLiveAgentOfficialReplyHttpDeps,
    register_legacy_live_agent_official_reply_route,
)
from agentsassemble.gui_legacy_live_agent_preflight_http import (
    LegacyLiveAgentPreflightHttpDeps,
    register_legacy_live_agent_preflight_route,
)
from agentsassemble.gui_legacy_live_agent_presence_http import (
    register_legacy_live_agent_presence_routes,
)
from agentsassemble.legacy.live_agent.http.probe import (
    LegacyLiveAgentProbeHttpDeps,
    register_legacy_live_agent_probe_route,
)
from agentsassemble.gui_legacy_live_agent_process_http import (
    LegacyProcessHttpDeps,
    register_legacy_process_mutation_routes,
)
from agentsassemble.gui_legacy_live_agent_read_http import (
    LegacyLiveAgentReadDeps,
    register_legacy_live_agent_read_routes,
)
from agentsassemble.gui_legacy_live_agent_readiness_http import (
    LegacyLiveAgentReadinessHttpDeps,
    register_legacy_live_agent_readiness_route,
)
from agentsassemble.legacy.live_agent.http.room_session import (
    register_legacy_room_session_route,
)
from agentsassemble.legacy.live_agent.http.self_managed import (
    register_legacy_self_managed_agent_routes,
)
from agentsassemble.gui_legacy_live_agent_session_http import (
    LegacySessionHttpDeps,
    register_legacy_session_mutation_routes,
)
from agentsassemble.gui_legacy_live_agent_session_run_http import (
    LegacySessionRunHttpDeps,
    register_legacy_session_run_basic_routes,
)
from agentsassemble.gui_legacy_live_agent_smoke_http import (
    LegacyLiveAgentSmokeHttpDeps,
    register_legacy_live_agent_smoke_routes,
)
from agentsassemble.legacy.live_agent.http.speech import (
    register_legacy_live_agent_speech_routes,
)
from agentsassemble.gui_legacy_lobby_http import register_legacy_lobby_routes
from agentsassemble.gui_legacy_meeting_http import register_legacy_meeting_routes
from agentsassemble.gui_legacy_meeting_lifecycle_http import (
    register_legacy_meeting_lifecycle_routes,
)
from agentsassemble.gui_legacy_official_round_http import (
    register_legacy_official_round_routes,
)
from agentsassemble.gui_legacy_official_turn_http import (
    register_legacy_official_turn_routes,
)
from agentsassemble.gui_legacy_provider_health_http import (
    register_legacy_provider_health_route,
)
from agentsassemble.gui_legacy_review_checkpoint_http import (
    register_legacy_review_checkpoint_route,
)
from agentsassemble.web.router import RequestContext, Router
from agentsassemble.legacy_codex_session_compat import (
    LegacyCodexSessionCompatibilityService,
)
from agentsassemble.legacy.live_agent.diagnostics import (
    LegacyLiveAgentDiagnosticQueryService,
)
from agentsassemble.legacy.live_agent.discovery import LegacyLiveAgentDiscoveryService
from agentsassemble.legacy.live_agent.engagement import LegacyLiveAgentEngagementService
from agentsassemble.legacy.live_agent.health_queries import (
    LegacyLiveAgentHealthQueryService,
)
from agentsassemble.legacy.live_agent.official_reply import (
    LegacyLiveAgentOfficialReplyService,
)
from agentsassemble.legacy.live_agent.preflight import LegacyLiveAgentPreflightService
from agentsassemble.legacy.live_agent.probe import LegacyLiveAgentProbeService
from agentsassemble.legacy.live_agent.process_service import (
    LegacyLiveAgentProcessMutationService,
    LegacyProcessMutationActions,
)
from agentsassemble.legacy.live_agent.queries import LegacyLiveAgentQueryService
from agentsassemble.legacy.live_agent.readiness import LegacyLiveAgentReadinessService
from agentsassemble.legacy.live_agent.roster_queries import (
    LegacyLiveAgentRosterQueryService,
)
from agentsassemble.legacy.live_agent.session_control import (
    session_check_error_message,
)
from agentsassemble.legacy.live_agent.session_service import (
    LegacyLiveAgentSessionMutationService,
    LegacySessionMutationActions,
)
from agentsassemble.legacy.live_agent.session_run_service import (
    LegacyLiveAgentSessionRunMutationService,
    LegacySessionRunActions,
)
from agentsassemble.legacy.live_agent.smoke import LegacyLiveAgentSmokeService
from agentsassemble.legacy.live_agent.speech import LegacyLiveAgentSpeechService
from agentsassemble.legacy_lobby_commands import LegacyLobbyCommandService
from agentsassemble.legacy_meeting_lifecycle import LegacyMeetingLifecycleService
from agentsassemble.legacy_meeting_queries import LegacyMeetingQueryService
from agentsassemble.legacy_official_rounds import LegacyOfficialRoundService
from agentsassemble.legacy_official_turns import LegacyOfficialTurnService
from agentsassemble.legacy_review_checkpoint import LegacyReviewCheckpointService
from agentsassemble.live_agent_processes import LiveAgentProcessSupervisor
from agentsassemble.legacy.live_agent.presence import LegacyLiveAgentPresenceService
from agentsassemble.live_agent_room_admin import LegacyLiveAgentRoomSessionService
from agentsassemble.live_agent_self_managed import LegacySelfManagedAgentService
from agentsassemble.live_agent_session_runs import LiveAgentSessionRunController
from agentsassemble.room.repository import RoomRepository


LegacyCallable = Callable[..., object]
OperationPayloadReader = Callable[
    [RequestContext, str, str],
    dict[str, object] | None,
]


@dataclass(frozen=True)
class LegacySmokeHooks:
    probe: LegacyCallable
    basic: LegacyCallable
    official_round: LegacyCallable
    session: LegacyCallable
    real_session: LegacyCallable


@dataclass(frozen=True)
class LegacySessionHooks:
    start: LegacyCallable
    ensure: LegacyCallable
    resume: LegacyCallable
    resume_agent: LegacyCallable
    agent_timing: LegacyCallable
    agent_options: LegacyCallable
    check: LegacyCallable
    restart: LegacyCallable
    recover: LegacyCallable
    stop: LegacyCallable
    stop_agent: LegacyCallable


@dataclass(frozen=True)
class LegacyProcessHooks:
    start: LegacyCallable
    stop_running: LegacyCallable
    stop: LegacyCallable
    restart: LegacyCallable
    recover: LegacyCallable


@dataclass(frozen=True)
class LegacySessionRunHooks:
    should_reconcile: LegacyCallable
    reconcile: LegacyCallable
    assert_launch_approved: LegacyCallable
    ensure: LegacyCallable


@dataclass(frozen=True)
class LegacyGuiPatchHooks:
    """Verified patch seams retained by GUI tests and compatibility callers."""

    turn_request: LegacyCallable
    provider_health_report: LegacyCallable
    smoke: LegacySmokeHooks
    session: LegacySessionHooks
    process: LegacyProcessHooks
    session_run: LegacySessionRunHooks


@dataclass(frozen=True)
class LegacyGuiApplication:
    output_root: Path
    processes: LiveAgentProcessSupervisor
    session_runs: LiveAgentSessionRunController
    session_run_monitor: SessionRunMonitor
    room_repository: RoomRepository
    append_lobby_event: LegacyCallable
    public_lobby_allows_room_scope: Callable[[dict[str, object]], bool]
    is_muted: LegacyCallable
    remote_lobby_requester: Callable[[], object | None]
    turn_adapter: Callable[[], LegacyCallable]
    read_operation_payload: OperationPayloadReader
    record_operation: LegacyCallable
    speech: LegacyLiveAgentSpeechService
    hooks: LegacyGuiPatchHooks
    session_run_actions_override: LegacySessionRunActions | None = None

    def register_meeting_routes(self, router: Router) -> None:
        """Register the retained meeting/lobby compatibility surface."""

        register_legacy_meeting_routes(
            router,
            queries=LegacyMeetingQueryService(self.output_root),
        )
        register_legacy_meeting_lifecycle_routes(
            router,
            service=LegacyMeetingLifecycleService(self.output_root),
        )
        register_legacy_review_checkpoint_route(
            router,
            service=LegacyReviewCheckpointService(
                output_root=self.output_root,
                process_supervisor=self.processes,
                turn_requester=self.hooks.turn_request,
            ),
        )
        register_legacy_official_turn_routes(
            router,
            service=LegacyOfficialTurnService(self.output_root),
        )
        register_legacy_official_round_routes(
            router,
            service=LegacyOfficialRoundService(self.output_root),
        )

        def enqueue_auto_turn(event: dict[str, object]) -> None:
            enqueue_agent_session_auto_turn_for_lobby_event(
                self.output_root,
                event,
                turn_adapter=self.turn_adapter(),
                repository=self.room_repository,
            )

        register_legacy_lobby_routes(
            router,
            commands=LegacyLobbyCommandService(
                output_root=self.output_root,
                append_lobby_event=self.append_lobby_event,
                public_lobby_allows_room_scope=self.public_lobby_allows_room_scope,
                is_muted=self.is_muted,
                requester=self.remote_lobby_requester,
            ),
            enqueue_auto_turn=enqueue_auto_turn,
        )

    def register_live_agent_routes(self, router: Router) -> None:
        """Register retained resident/live-agent compatibility routes."""

        health = LegacyLiveAgentHealthQueryService(
            output_root=self.output_root,
            processes=self.processes,
            session_run_monitor=self.session_run_monitor,
        )
        register_legacy_live_agent_read_routes(
            router,
            deps=LegacyLiveAgentReadDeps(
                queries=LegacyLiveAgentQueryService.build(self.output_root),
                roster=LegacyLiveAgentRosterQueryService(self.output_root),
                health=health,
                diagnostics=LegacyLiveAgentDiagnosticQueryService(
                    output_root=self.output_root,
                    processes=self.processes,
                    session_run_controller=self.session_runs,
                ),
                readiness_error_message=session_check_error_message,
            ),
        )
        register_legacy_live_agent_presence_routes(
            router,
            service=LegacyLiveAgentPresenceService(self.output_root),
        )
        register_legacy_live_agent_engagement_route(
            router,
            service=LegacyLiveAgentEngagementService(self.output_root),
        )
        register_legacy_live_agent_join_brief_route(
            router,
            request_server_url=lambda ctx: ctx.request_server_url(),
        )
        register_legacy_provider_health_route(
            router,
            reporter=self.hooks.provider_health_report,
        )
        register_legacy_codex_session_routes(
            router,
            deps=LegacyCodexSessionHttpDeps(
                sessions=LegacyCodexSessionCompatibilityService(
                    output_root=self.output_root,
                    processes=self.processes,
                    ensure_session=self.hooks.session.ensure,
                    restart_session=self.hooks.session.restart,
                    record_operation=self.record_operation,
                ),
                read_operation_payload=self.read_operation_payload,
                request_server_url=lambda ctx: ctx.request_server_url(),
            ),
        )
        register_legacy_live_agent_official_reply_route(
            router,
            deps=LegacyLiveAgentOfficialReplyHttpDeps(
                replies=LegacyLiveAgentOfficialReplyService(self.output_root),
                read_operation_payload=self.read_operation_payload,
            ),
        )
        register_legacy_live_agent_probe_route(
            router,
            deps=LegacyLiveAgentProbeHttpDeps(
                probe=LegacyLiveAgentProbeService(
                    self.output_root,
                    probe_runner=self.hooks.smoke.probe,
                ),
                read_operation_payload=self.read_operation_payload,
            ),
        )
        register_legacy_live_agent_speech_routes(router, service=self.speech)
        register_legacy_live_agent_preflight_route(
            router,
            deps=LegacyLiveAgentPreflightHttpDeps(
                preflight=LegacyLiveAgentPreflightService(),
                read_operation_payload=self.read_operation_payload,
                record_operation=self.record_operation,
                request_server_url=lambda ctx: ctx.request_server_url(),
            ),
        )
        register_legacy_live_agent_discovery_route(
            router,
            deps=LegacyLiveAgentDiscoveryHttpDeps(
                discovery=LegacyLiveAgentDiscoveryService(self.output_root),
                read_operation_payload=self.read_operation_payload,
                record_operation=self.record_operation,
                request_server_url=lambda ctx: ctx.request_server_url(),
            ),
        )

        smoke = LegacyLiveAgentSmokeService(
            self.output_root,
            basic_smoke_runner=lambda **kwargs: self.hooks.smoke.basic(**kwargs),
            official_round_smoke_runner=lambda **kwargs: self.hooks.smoke.official_round(
                **kwargs
            ),
            session_smoke_runner=lambda **kwargs: self.hooks.smoke.session(**kwargs),
            real_session_smoke_runner=lambda **kwargs: self.hooks.smoke.real_session(
                **kwargs
            ),
        )
        register_legacy_live_agent_smoke_routes(
            router,
            deps=LegacyLiveAgentSmokeHttpDeps(
                smoke=smoke,
                read_operation_payload=self.read_operation_payload,
                record_operation=self.record_operation,
                local_server_url=lambda ctx: ctx.local_server_url(),
            ),
        )
        register_legacy_live_agent_readiness_route(
            router,
            deps=LegacyLiveAgentReadinessHttpDeps(
                readiness=LegacyLiveAgentReadinessService(
                    output_root=self.output_root,
                    processes=self.processes,
                    health=health,
                    smoke=smoke,
                    probe_runner=self.hooks.smoke.probe,
                ),
                read_operation_payload=self.read_operation_payload,
                record_operation=self.record_operation,
                local_server_url=lambda ctx: ctx.local_server_url(),
            ),
        )

        session_service = LegacyLiveAgentSessionMutationService(
            self.output_root,
            processes=self.processes,
            session_runs=self.session_runs,
            actions=LegacySessionMutationActions(
                start=self.hooks.session.start,
                ensure=self.hooks.session.ensure,
                resume=self.hooks.session.resume,
                resume_agent=self.hooks.session.resume_agent,
                agent_timing=self.hooks.session.agent_timing,
                agent_options=self.hooks.session.agent_options,
                check=self.hooks.session.check,
                restart=self.hooks.session.restart,
                recover=self.hooks.session.recover,
                stop=self.hooks.session.stop,
                stop_agent=self.hooks.session.stop_agent,
            ),
            record_operation=self.record_operation,
        )
        register_legacy_session_mutation_routes(
            router,
            deps=LegacySessionHttpDeps(
                service=session_service,
                read_operation_payload=self.read_operation_payload,
                default_server_url=lambda ctx: ctx.request_server_url(),
            ),
        )

        process_service = LegacyLiveAgentProcessMutationService(
            self.output_root,
            processes=self.processes,
            actions=LegacyProcessMutationActions(
                start=self.hooks.process.start,
                stop_running=self.hooks.process.stop_running,
                stop=self.hooks.process.stop,
                restart=self.hooks.process.restart,
                recover=self.hooks.process.recover,
            ),
            record_operation=self.record_operation,
        )
        register_legacy_process_mutation_routes(
            router,
            deps=LegacyProcessHttpDeps(
                service=process_service,
                read_operation_payload=self.read_operation_payload,
                default_server_url=lambda ctx: ctx.request_server_url(),
            ),
        )

        session_run_actions = self.session_run_actions_override or LegacySessionRunActions(
            should_reconcile=self.hooks.session_run.should_reconcile,
            reconcile=self.hooks.session_run.reconcile,
            assert_launch_approved=self.hooks.session_run.assert_launch_approved,
            ensure=self.hooks.session_run.ensure,
        )
        register_legacy_session_run_basic_routes(
            router,
            deps=LegacySessionRunHttpDeps(
                service=LegacyLiveAgentSessionRunMutationService(
                    self.output_root,
                    session_runs=self.session_runs,
                    actions=session_run_actions,
                    record_operation=self.record_operation,
                ),
                read_operation_payload=self.read_operation_payload,
                default_server_url=lambda ctx: ctx.request_server_url(),
            ),
        )
        register_legacy_self_managed_agent_routes(
            router,
            service=LegacySelfManagedAgentService(self.output_root),
        )
        register_legacy_room_session_route(
            router,
            service=LegacyLiveAgentRoomSessionService(
                self.output_root,
                self.processes,
            ),
        )
