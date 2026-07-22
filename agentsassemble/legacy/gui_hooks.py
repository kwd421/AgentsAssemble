"""Compatibility hook composition for retained GUI routes."""
from __future__ import annotations

from collections.abc import Callable

from agentsassemble.legacy.gui_application import (
    LegacyGuiPatchHooks,
    LegacyProcessHooks,
    LegacySessionHooks,
    LegacySessionRunHooks,
    LegacySmokeHooks,
)


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
