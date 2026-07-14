"""Aggregate readiness policy for retained resident agents."""

from __future__ import annotations

import math
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.legacy_live_agent_health_queries import LegacyLiveAgentHealthQueryService
from agentsassemble.legacy_live_agent_readiness_projection import (
    OFFICIAL_ROUND_SMOKE_ERROR,
    SESSION_SMOKE_ERROR,
    payload_probe_ids,
    safe_readiness_official_round_smoke_result,
    safe_readiness_probe_groups,
    safe_readiness_probe_result,
    safe_readiness_session_smoke_result,
    safe_readiness_smoke_result,
)
from agentsassemble.legacy_live_agent_smoke import (
    LegacyLiveAgentSmokeService,
    session_smoke_soak_cycle_count,
    session_smoke_soak_interval_seconds,
)
from agentsassemble.live_agent_probe import run_live_agent_probe, safe_probe_timeout
from agentsassemble.live_agent_processes import LiveAgentProcessSupervisor
from agentsassemble.live_agent_smoke import LiveAgentSmokeFailed
from agentsassemble.session_run_monitor import PeriodicSessionRunMonitor


MAX_READINESS_PROBE_AGENTS = 10
ProbeRunner = Callable[..., dict[str, object]]


@dataclass(frozen=True)
class LegacyLiveAgentReadinessService:
    output_root: Path
    processes: LiveAgentProcessSupervisor
    health: LegacyLiveAgentHealthQueryService
    smoke: LegacyLiveAgentSmokeService
    probe_runner: ProbeRunner = run_live_agent_probe

    def check(self, payload: dict[str, object], *, default_server: str) -> dict[str, object]:
        return _build_readiness(
            self.output_root,
            payload,
            default_server=default_server,
            health=self.health.health(),
            groups=self.processes.snapshot_groups(),
            smoke=self.smoke,
            probe_runner=self.probe_runner,
        )


def live_agent_readiness_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
    session_run_monitor: PeriodicSessionRunMonitor | None = None,
    smoke: LegacyLiveAgentSmokeService | None = None,
    probe_runner: ProbeRunner = run_live_agent_probe,
) -> dict[str, object]:
    health = LegacyLiveAgentHealthQueryService(
        output_root=output_root,
        processes=process_supervisor,
        session_run_monitor=session_run_monitor,
    )
    return _build_readiness(
        output_root,
        payload,
        default_server=default_server,
        health=health.health(),
        groups=process_supervisor.snapshot_groups(),
        smoke=smoke or LegacyLiveAgentSmokeService(output_root),
        probe_runner=probe_runner,
    )


def _build_readiness(
    output_root: Path,
    payload: dict[str, object],
    *,
    default_server: str,
    health: dict[str, object],
    groups: list[dict[str, object]],
    smoke: LegacyLiveAgentSmokeService,
    probe_runner: ProbeRunner,
) -> dict[str, object]:
    checks = [{"id": "health", "status": health.get("status") or "unknown"}]
    invalid_probe_payload = _invalid_probe_id_payload(payload.get("probe_agent_ids")) or _invalid_probe_id_payload(
        payload.get("probe_group_ids")
    )
    probe_plan = _readiness_probe_plan(
        groups,
        requested_agent_ids=payload_probe_ids(payload.get("probe_agent_ids")),
        requested_group_ids=payload_probe_ids(payload.get("probe_group_ids")),
    )
    probe_agent_ids = list(probe_plan["agent_ids"])
    probe_groups = list(probe_plan["probe_groups"])
    probe_timeout = safe_probe_timeout(
        _nonnegative_float(payload.get("probe_timeout_seconds", payload.get("timeout")), 12.0)
    )
    probe_error = ""
    official_round_requested = _payload_bool(payload.get("official_round_smoke"))
    session_smoke_requested = _payload_bool(payload.get("session_smoke"))
    if invalid_probe_payload:
        probe_error = "Invalid probe id payload; expected a list of strings."
    elif len(probe_agent_ids) > MAX_READINESS_PROBE_AGENTS:
        probe_error = f"Too many probe agents requested; maximum is {MAX_READINESS_PROBE_AGENTS}."

    try:
        basic_smoke = safe_readiness_smoke_result(
            smoke.run_basic(payload, default_server=default_server)
        )
    except LiveAgentSmokeFailed as error:
        basic_smoke = safe_readiness_smoke_result(
            {
                "status": "failed",
                "group_id": str(payload.get("group_id") or ""),
                "error": str(error),
            }
        )
    checks.append({"id": "smoke", "status": basic_smoke.get("status") or "unknown"})

    official_round_smoke: dict[str, object] = {}
    if official_round_requested and basic_smoke.get("status") == "ok":
        try:
            official_round_smoke = safe_readiness_official_round_smoke_result(
                smoke.run_official_round(payload, default_server=default_server)
            )
        except (LiveAgentSmokeFailed, ValueError, urllib.error.URLError):
            official_round_smoke = safe_readiness_official_round_smoke_result(
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
    if session_smoke_requested and basic_smoke.get("status") == "ok":
        try:
            session_smoke = safe_readiness_session_smoke_result(
                smoke.run_session(
                    {
                        "timeout": _nonnegative_float(payload.get("timeout"), 12.0),
                        "lobby_probe_count": _nonnegative_int(payload.get("session_smoke_lobby_probe_count"), 1),
                        "soak_cycle_count": session_smoke_soak_cycle_count(
                            payload.get("session_smoke_soak_cycle_count")
                        ),
                        "soak_interval_seconds": session_smoke_soak_interval_seconds(
                            payload.get("session_smoke_soak_interval_seconds")
                        ),
                    },
                    default_server=default_server,
                )
            )
        except (LiveAgentSmokeFailed, ValueError, urllib.error.URLError):
            session_smoke = safe_readiness_session_smoke_result(
                {"status": "failed", "error": SESSION_SMOKE_ERROR}
            )
        checks.append({"id": "session_smoke", "status": session_smoke.get("status") or "unknown"})
    elif session_smoke_requested:
        session_smoke = {"status": "skipped", "reason": "smoke did not pass"}
        checks.append({"id": "session_smoke", "status": "skipped"})

    probes: list[dict[str, object]] = []
    probe_group_failed = any(group.get("status") != "ok" for group in probe_groups)
    if basic_smoke.get("status") == "ok":
        for group in probe_groups:
            checks.append(
                {
                    "id": f"probe_group:{group.get('group_id') or 'unknown'}",
                    "status": group.get("status") or "unknown",
                }
            )
    if basic_smoke.get("status") == "ok" and (probe_error or probe_group_failed):
        if probe_error:
            check_id = "probe_request_payload" if invalid_probe_payload else "probe_request_limit"
            checks.append({"id": check_id, "status": "failed"})
    elif basic_smoke.get("status") == "ok":
        for agent_id in probe_agent_ids:
            try:
                probe = probe_runner(output_root, agent_id, timeout_seconds=probe_timeout)
            except ValueError:
                probe = {"status": "failed", "agent_id": agent_id, "reason": "probe could not be run"}
            safe_probe = safe_readiness_probe_result(probe)
            probes.append(safe_probe)
            checks.append({"id": f"probe:{agent_id}", "status": safe_probe.get("status") or "unknown"})

    if basic_smoke.get("status") != "ok":
        status = "failed"
    elif official_round_requested and official_round_smoke.get("status") != "ok":
        status = "failed"
    elif session_smoke_requested and session_smoke.get("status") != "ok":
        status = "failed"
    elif probe_group_failed or probe_error:
        status = "failed"
    elif any(probe.get("status") != "ok" for probe in probes):
        status = "failed"
    elif health.get("status") != "ok":
        status = "degraded"
    else:
        status = "ready"

    result: dict[str, object] = {
        "status": status,
        "checks": checks,
        "health": health,
        "smoke": basic_smoke,
    }
    if official_round_smoke:
        result["official_round_smoke"] = official_round_smoke
    if session_smoke:
        result["session_smoke"] = session_smoke
    if probe_error:
        result["probe_error"] = probe_error
    if probe_groups:
        result["probe_groups"] = safe_readiness_probe_groups(
            probe_groups,
            include_agent_ids=not probe_error,
        )
    if probe_agent_ids and not probe_error and not probe_group_failed:
        result["effective_probe_agent_ids"] = probe_agent_ids
    if probes:
        result["probes"] = probes
    return result


def _readiness_probe_plan(
    groups: list[dict[str, object]],
    *,
    requested_agent_ids: list[str],
    requested_group_ids: list[str],
) -> dict[str, list[dict[str, object]] | list[str]]:
    agent_ids = list(dict.fromkeys(requested_agent_ids))
    seen_agents = set(agent_ids)
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
    if not isinstance(value, list):
        return []
    agent_ids = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("agent_id") or "").strip()[:64]
        if not agent_id or agent_id in seen:
            continue
        seen.add(agent_id)
        agent_ids.append(agent_id)
    return agent_ids


def _invalid_probe_id_payload(value: object) -> bool:
    if value is None:
        return False
    if not isinstance(value, list):
        return True
    return any(not isinstance(item, str) for item in value)


def _payload_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}


def _nonnegative_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (OverflowError, TypeError, ValueError):
        return default
    return max(0, parsed)


def _nonnegative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(0.0, parsed)


def _result_status(value: object) -> str:
    return str(value or "unknown").strip() or "unknown"
