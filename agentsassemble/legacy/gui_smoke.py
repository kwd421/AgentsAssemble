"""Smoke and aggregate readiness payloads retained by the legacy GUI API."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agentsassemble.legacy.gui_payload import payload_nonnegative_float
from agentsassemble.legacy.live_agent.readiness import live_agent_readiness_payload
from agentsassemble.legacy.live_agent.smoke import (
    LegacyLiveAgentSmokeService,
    live_agent_real_session_smoke_payload as resident_real_session_smoke_payload,
    live_agent_session_smoke_payload as resident_session_smoke_payload,
)


Runner = Callable[..., dict[str, object]]


def basic_smoke_payload(
    payload: dict[str, object],
    *,
    default_server: str,
    request_json: Callable[..., dict[str, object]],
    runner: Runner,
) -> dict[str, object]:
    return runner(
        server=default_server,
        group_id=str(payload.get("group_id") or ""),
        timeout_seconds=payload_nonnegative_float(payload.get("timeout"), 12.0),
        request_json=request_json,
    )


def official_round_smoke_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    default_server: str,
    request_json: Callable[..., dict[str, object]],
    runner: Runner,
) -> dict[str, object]:
    return runner(
        output_root=output_root,
        server=default_server,
        group_id=str(payload.get("group_id") or ""),
        timeout_seconds=payload_nonnegative_float(payload.get("timeout"), 12.0),
        request_json=request_json,
    )


def session_smoke_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    default_server: str,
    request_json: Callable[..., dict[str, object]],
    runner: Runner,
) -> dict[str, object]:
    return resident_session_smoke_payload(
        output_root,
        payload,
        default_server=default_server,
        request_json=request_json,
        runner=runner,
    )


def real_session_smoke_payload(
    output_root: Path,
    payload: dict[str, object],
    *,
    default_server: str,
    request_json: Callable[..., dict[str, object]],
    runner: Runner,
) -> dict[str, object]:
    return resident_real_session_smoke_payload(
        output_root,
        payload,
        default_server=default_server,
        request_json=request_json,
        runner=runner,
    )


def aggregate_readiness_payload(
    output_root: Path,
    process_supervisor: object,
    payload: dict[str, object],
    *,
    default_server: str,
    session_run_monitor: object | None,
    basic_smoke_runner: Runner,
    official_round_smoke_runner: Runner,
    session_smoke_runner: Runner,
    real_session_smoke_runner: Runner,
    probe_runner: Runner,
) -> dict[str, object]:
    smoke = LegacyLiveAgentSmokeService(
        output_root,
        basic_smoke_runner=basic_smoke_runner,
        official_round_smoke_runner=official_round_smoke_runner,
        session_smoke_runner=session_smoke_runner,
        real_session_smoke_runner=real_session_smoke_runner,
    )
    return live_agent_readiness_payload(
        output_root,
        process_supervisor,
        payload,
        default_server=default_server,
        session_run_monitor=session_run_monitor,
        smoke=smoke,
        probe_runner=probe_runner,
    )
