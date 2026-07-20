from __future__ import annotations

import argparse
import json
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


MAX_READINESS_PROBE_AGENTS = 10
DIAGNOSTIC_COMMANDS = {"doctor", "probe"}


@dataclass(frozen=True)
class DiagnosticCliRuntime:
    request_json: Callable[..., dict[str, object]]
    server_url: Callable[[str, str], str]
    operation_http_timeout: Callable[..., float]
    session_smoke_http_timeout: Callable[..., float]
    probe_http_timeout: Callable[[float], float]
    provider_health_report: Callable[..., dict[str, object]]
    format_provider_health: Callable[[dict[str, object]], str]
    format_readiness: Callable[[dict[str, object]], str]
    format_probe: Callable[[dict[str, object]], str]


def run_diagnostic_command(args: argparse.Namespace, *, runtime: DiagnosticCliRuntime) -> int | None:
    command = str(getattr(args, "live_agent_command", ""))
    if command not in DIAGNOSTIC_COMMANDS:
        return None
    if command == "doctor":
        return _doctor(args, runtime)
    return _probe(args, runtime)


def run_provider_health_command(args: argparse.Namespace, *, runtime: DiagnosticCliRuntime) -> int:
    report = runtime.provider_health_report(
        Path(args.config),
        probe_mode=args.probe_mode,
        probe_timeout_seconds=args.probe_timeout,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.as_json else runtime.format_provider_health(report))
    return 0 if report.get("status") == "ok" else 1


def _doctor(args: argparse.Namespace, runtime: DiagnosticCliRuntime) -> int:
    payload: dict[str, object] = {"group_id": args.group_id, "timeout": float(args.timeout)}
    if args.official_round_smoke:
        payload["official_round_smoke"] = True
    if args.session_smoke:
        payload["session_smoke"] = True
        if int(args.session_smoke_soak_cycles):
            payload["session_smoke_soak_cycle_count"] = int(args.session_smoke_soak_cycles)
        if float(args.session_smoke_soak_interval):
            payload["session_smoke_soak_interval_seconds"] = float(args.session_smoke_soak_interval)
    if args.probe_agent_ids:
        payload["probe_agent_ids"] = list(args.probe_agent_ids)
    if args.probe_group_ids:
        payload["probe_group_ids"] = list(args.probe_group_ids)
    probe_windows = (
        MAX_READINESS_PROBE_AGENTS
        if args.probe_group_ids
        else min(len(args.probe_agent_ids), MAX_READINESS_PROBE_AGENTS)
    )
    official_windows = 4 if args.official_round_smoke else 0
    timeout_seconds = runtime.operation_http_timeout(
        float(args.timeout),
        windows=1 + official_windows + probe_windows,
    )
    if args.session_smoke:
        timeout_seconds += runtime.session_smoke_http_timeout(
            float(args.timeout),
            soak_cycle_count=int(args.session_smoke_soak_cycles),
            soak_interval_seconds=float(args.session_smoke_soak_interval),
        )
    result = runtime.request_json(
        runtime.server_url(args.server, "/api/live-agent-readiness"),
        method="POST",
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.as_json else runtime.format_readiness(result))
    return 0 if result.get("status") == "ready" else 1


def _probe(args: argparse.Namespace, runtime: DiagnosticCliRuntime) -> int:
    agent_id = urllib.parse.quote(args.agent_id, safe="")
    result = runtime.request_json(
        runtime.server_url(args.server, f"/api/live-agents/{agent_id}/probe"),
        method="POST",
        payload={"timeout_seconds": float(args.timeout)},
        timeout_seconds=runtime.probe_http_timeout(float(args.timeout)),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.as_json else runtime.format_probe(result))
    return 0 if result.get("status") == "ok" else 1
