"""Compatibility hook composition for retained GUI routes."""
from __future__ import annotations

from collections.abc import Callable

from agentsassemble.legacy.gui_application import (
    LegacyGuiApplication,
    LegacyGuiPatchHooks,
    LegacyProcessHooks,
    LegacySessionHooks,
    LegacySessionRunHooks,
    LegacySmokeHooks,
)
from agentsassemble.legacy.live_agent.http.flow import register_live_agent_flow_routes
from agentsassemble.legacy.meeting.http.room_composition import register_room_routes
from agentsassemble.legacy.runtime_policy import quarantined_legacy_router
from agentsassemble.web.router import Router


def build_legacy_gui_patch_hooks(
    *,
    turn_request: Callable[..., object],
    provider_health_report: Callable[..., object],
    probe: Callable[..., object],
    basic_smoke: Callable[..., object],
    official_round_smoke: Callable[..., object],
    session_smoke: Callable[..., object],
    real_session_smoke: Callable[..., object],
    session_start: Callable[..., object],
    session_ensure: Callable[..., object],
    session_resume: Callable[..., object],
    session_resume_agent: Callable[..., object],
    session_agent_timing: Callable[..., object],
    session_agent_options: Callable[..., object],
    session_check: Callable[..., object],
    session_restart: Callable[..., object],
    session_recover: Callable[..., object],
    session_stop: Callable[..., object],
    session_stop_agent: Callable[..., object],
    process_start: Callable[..., object],
    process_stop_running: Callable[..., object],
    process_stop: Callable[..., object],
    process_restart: Callable[..., object],
    process_recover: Callable[..., object],
    session_run_should_reconcile: Callable[..., object],
    session_run_reconcile: Callable[..., object],
    session_run_assert_launch_approved: Callable[..., object],
    session_run_ensure: Callable[..., object],
) -> LegacyGuiPatchHooks:
    return LegacyGuiPatchHooks(
        turn_request=turn_request,
        provider_health_report=provider_health_report,
        smoke=LegacySmokeHooks(
            probe=probe,
            basic=basic_smoke,
            official_round=official_round_smoke,
            session=session_smoke,
            real_session=real_session_smoke,
        ),
        session=LegacySessionHooks(
            start=session_start,
            ensure=session_ensure,
            resume=session_resume,
            resume_agent=session_resume_agent,
            agent_timing=session_agent_timing,
            agent_options=session_agent_options,
            check=session_check,
            restart=session_restart,
            recover=session_recover,
            stop=session_stop,
            stop_agent=session_stop_agent,
        ),
        process=LegacyProcessHooks(
            start=process_start,
            stop_running=process_stop_running,
            stop=process_stop,
            restart=process_restart,
            recover=process_recover,
        ),
        session_run=LegacySessionRunHooks(
            should_reconcile=session_run_should_reconcile,
            reconcile=session_run_reconcile,
            assert_launch_approved=session_run_assert_launch_approved,
            ensure=session_run_ensure,
        ),
    )


def register_legacy_gui_routes(
    route_table: Router,
    *,
    legacy_application: LegacyGuiApplication,
    flow: object,
    read_operation_payload: Callable[..., dict[str, object] | None],
    record_operation: Callable[..., object],
) -> None:
    # Despite its retained import location, ``register_room_routes`` is the
    # canonical room coordinator: it owns current room, invite, attachment,
    # member, voice, and agent-session APIs. Keep it on the real router until
    # those domains have moved to a non-legacy package.
    register_room_routes(route_table)

    # Only the explicitly retained meeting/live-agent compatibility surface is
    # placed behind the mutation quarantine.
    legacy_routes = quarantined_legacy_router(route_table)
    legacy_application.register_meeting_routes(legacy_routes)
    register_live_agent_flow_routes(
        legacy_routes,
        flow=flow,
        is_loopback_request=lambda ctx: ctx.uses_loopback_host(),
        read_operation_payload=read_operation_payload,
        record_operation=record_operation,
    )
    legacy_application.register_live_agent_routes(legacy_routes)
