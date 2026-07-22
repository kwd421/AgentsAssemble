"""Durable retained session-run reconciliation used by the GUI."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from agentsassemble.application.session_run_monitor import (
    PeriodicSessionRunMonitor,
    safe_monitor_error_type,
)
from agentsassemble.legacy.live_agent.health import (
    DEFAULT_SESSION_RUN_MONITOR_INTERVAL_SECONDS,
    MIN_SESSION_RUN_MONITOR_INTERVAL_SECONDS,
)
from agentsassemble.legacy.live_agent.runtime.launch_policy import (
    APPROVAL_REQUIRED_MESSAGE,
    assert_resident_launch_approved,
)
from agentsassemble.legacy.live_agent.runtime.processes import LiveAgentProcessSupervisor
from agentsassemble.legacy.live_agent.runtime.session_runs import LiveAgentSessionRunController


SESSION_RUN_MONITOR_ERROR = "Live-agent session run monitor failed."


@dataclass(frozen=True)
class LegacyGuiSessionRunRuntime:
    ensure_payload: Callable[..., dict[str, object]]
    readiness_payload: Callable[..., dict[str, object]]
    ready_session_requires_restart: Callable[..., bool]
    record_operation: Callable[..., object]
    payload_bool: Callable[[object], bool]


def reconcile_session_runs_on_startup(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    session_run_controller: LiveAgentSessionRunController,
    *,
    default_server: str,
    runtime: LegacyGuiSessionRunRuntime,
) -> list[dict[str, object]]:
    return reconcile_session_runs(
        output_root,
        process_supervisor,
        session_run_controller,
        default_server=default_server,
        summary="reconciled durable live-agent session runs on GUI startup",
        runtime=runtime,
    )


def reconcile_session_runs(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    session_run_controller: LiveAgentSessionRunController,
    *,
    default_server: str,
    summary: str,
    runtime: LegacyGuiSessionRunRuntime,
    target_run_id: str = "",
    request_overrides: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    def ensure_from_run(run: dict[str, object]) -> dict[str, object]:
        request = session_run_reconcile_request(run)
        if isinstance(request_overrides, dict):
            request.update(request_overrides)
        assert_session_run_launch_approved(
            process_supervisor,
            request,
            default_server,
            payload_bool=runtime.payload_bool,
        )
        return runtime.ensure_payload(
            output_root,
            process_supervisor,
            request,
            default_server=default_server,
        )

    results = session_run_controller.reconcile_active_runs(
        ensure_from_run,
        should_reconcile=lambda run: session_run_monitor_should_reconcile(
            output_root,
            process_supervisor,
            run,
            target_run_id=target_run_id,
            runtime=runtime,
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
        runtime.record_operation(
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


def session_run_reconcile_request(run: dict[str, object]) -> dict[str, object]:
    request = dict(run.get("request") if isinstance(run.get("request"), dict) else {})
    meeting_id = str(run.get("meeting_id") or "").strip()
    group_id = str(run.get("group_id") or "").strip()
    if meeting_id:
        request["meeting_id"] = meeting_id
    if group_id:
        request["group_id"] = group_id
    return request


def session_run_reconcile_launch_policy_targets(
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


def assert_session_run_launch_approved(
    process_supervisor: LiveAgentProcessSupervisor,
    request: dict[str, object],
    default_server: str,
    *,
    payload_bool: Callable[[object], bool],
) -> None:
    approved = payload_bool(request.get("approve_real_providers"))
    for config_path, server in session_run_reconcile_launch_policy_targets(
        process_supervisor,
        request,
        default_server,
    ):
        assert_resident_launch_approved(
            config_path,
            request=request,
            server=server,
            approved=approved,
        )


def session_run_monitor_should_reconcile(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    run: dict[str, object],
    *,
    runtime: LegacyGuiSessionRunRuntime,
    target_run_id: str = "",
) -> bool:
    if target_run_id and str(run.get("run_id") or "") != target_run_id:
        return False
    if str(run.get("status") or "unknown").strip() != "ready":
        return True
    meeting_id = str(run.get("meeting_id") or "").strip()
    group_id = str(run.get("group_id") or "").strip()
    if not meeting_id or not group_id:
        return True
    try:
        readiness = runtime.readiness_payload(
            output_root,
            process_supervisor,
            meeting_id=meeting_id,
            group_id=group_id,
        )
    except (OSError, ValueError):
        return True
    if str(readiness.get("status") or "unknown").strip() != "ready":
        return True
    return runtime.ready_session_requires_restart(
        output_root,
        process_supervisor,
        {"meeting_id": meeting_id, "group_id": group_id},
        readiness,
    )


class LegacyGuiSessionRunMonitor(PeriodicSessionRunMonitor):
    def __init__(
        self,
        output_root: Path,
        process_supervisor: LiveAgentProcessSupervisor,
        session_run_controller: LiveAgentSessionRunController,
        *,
        default_server: str,
        runtime: LegacyGuiSessionRunRuntime,
        interval_seconds: float = DEFAULT_SESSION_RUN_MONITOR_INTERVAL_SECONDS,
    ) -> None:
        self.output_root = output_root
        self.process_supervisor = process_supervisor
        self.session_run_controller = session_run_controller
        self.default_server = default_server
        self.runtime = runtime
        super().__init__(
            reconcile_runs=lambda: reconcile_session_runs(
                self.output_root,
                self.process_supervisor,
                self.session_run_controller,
                default_server=self.default_server,
                summary="reconciled durable live-agent session runs during GUI runtime",
                runtime=self.runtime,
            ),
            report_failure=self._report_failure,
            interval_seconds=interval_seconds,
            default_interval_seconds=DEFAULT_SESSION_RUN_MONITOR_INTERVAL_SECONDS,
            minimum_interval_seconds=MIN_SESSION_RUN_MONITOR_INTERVAL_SECONDS,
        )

    def _report_failure(self, error: Exception) -> None:
        self.runtime.record_operation(
            self.output_root,
            operation="session_run.monitor",
            status="failed",
            summary="live-agent session-run monitor failed",
            error=SESSION_RUN_MONITOR_ERROR,
            details={"error_type": safe_monitor_error_type(error)},
        )
