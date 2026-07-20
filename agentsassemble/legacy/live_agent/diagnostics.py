"""Read-only history, process, and readiness queries for legacy residents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentsassemble.legacy.live_agent.health import live_agent_process_health_reason, safe_health_identity
from agentsassemble.legacy.live_agent.process_projection import live_agent_processes_payload
from agentsassemble.legacy.live_agent.runtime.operations import read_live_agent_operation_history
from agentsassemble.legacy.live_agent.runtime.processes import (
    LiveAgentProcessSupervisor,
    read_live_agent_process_event_history,
)
from agentsassemble.legacy.live_agent.runtime.session_runs import LiveAgentSessionRunController
from agentsassemble.legacy.live_agent.runtime.sessions import check_live_agent_session, live_agent_session_readiness_summary


@dataclass(frozen=True)
class LegacyLiveAgentDiagnosticQueryService:
    """Server-scoped read facade for retained resident diagnostics."""

    output_root: Path
    processes: LiveAgentProcessSupervisor
    session_run_controller: LiveAgentSessionRunController

    def operations(
        self,
        *,
        limit: int = 50,
        operation: str = "",
        target_id: str = "",
        status: str = "",
        scan_limit: object = None,
        scan_tail: bool = False,
    ) -> dict[str, object]:
        return live_agent_operations_payload(
            self.output_root,
            limit=limit,
            operation=operation,
            target_id=target_id,
            status=status,
            scan_limit=scan_limit,
            scan_tail=scan_tail,
        )

    def process_events(
        self,
        *,
        limit: int = 50,
        group_id: str = "",
        scan_limit: object = None,
    ) -> dict[str, object]:
        return live_agent_process_events_payload(
            self.output_root,
            limit=limit,
            group_id=group_id,
            scan_limit=scan_limit,
        )

    def process_groups(self) -> dict[str, object]:
        return live_agent_processes_payload(self.processes, output_root=self.output_root)

    def readiness(self, *, meeting_id: str, group_id: str) -> dict[str, object]:
        return live_agent_session_readiness_payload(
            self.output_root,
            self.processes,
            meeting_id=meeting_id,
            group_id=group_id,
        )

    def session_runs(
        self,
        *,
        limit: int = 50,
        run_id: str = "",
        meeting_id: str = "",
        group_id: str = "",
        include_readiness: bool = False,
    ) -> dict[str, object]:
        return live_agent_session_runs_payload(
            self.session_run_controller,
            limit=limit,
            run_id=run_id,
            meeting_id=meeting_id,
            group_id=group_id,
            include_readiness=include_readiness,
            output_root=self.output_root,
            process_supervisor=self.processes,
        )


def live_agent_operations_payload(
    output_root: Path,
    *,
    limit: int = 50,
    operation: str = "",
    target_id: str = "",
    status: str = "",
    scan_limit: object = None,
    scan_tail: bool = False,
) -> dict[str, object]:
    return read_live_agent_operation_history(
        output_root,
        limit=limit,
        operation=operation,
        target_id=target_id,
        status=status,
        scan_limit=scan_limit,
        scan_tail=scan_tail,
    )


def live_agent_process_events_payload(
    output_root: Path,
    *,
    limit: int = 50,
    group_id: str = "",
    scan_limit: object = None,
) -> dict[str, object]:
    return read_live_agent_process_event_history(
        output_root,
        limit=limit,
        group_id=group_id,
        scan_limit=scan_limit,
    )


def live_agent_session_runs_payload(
    session_run_controller: LiveAgentSessionRunController,
    *,
    limit: int = 50,
    run_id: str = "",
    meeting_id: str = "",
    group_id: str = "",
    include_readiness: bool = False,
    output_root: Path | None = None,
    process_supervisor: LiveAgentProcessSupervisor | None = None,
) -> dict[str, object]:
    runs = session_run_controller.list_runs(
        limit=limit,
        run_id=run_id,
        meeting_id=meeting_id,
        group_id=group_id,
    )
    if include_readiness and output_root is not None and process_supervisor is not None:
        runs = session_runs_with_readiness(
            runs,
            output_root=output_root,
            process_supervisor=process_supervisor,
        )
    return {"runs": runs}


def live_agent_session_check_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
) -> dict[str, object]:
    group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        raise ValueError("Live agent group id is required.")
    return _session_check_payload_with_process_reason(
        output_root,
        process_supervisor,
        meeting_id=str(payload.get("meeting_id") or ""),
        group_id=group_id,
    )


def live_agent_session_readiness_payload(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    *,
    meeting_id: str,
    group_id: str,
) -> dict[str, object]:
    if not str(group_id or "").strip():
        raise ValueError("Live agent group id is required.")
    return _session_check_payload_with_process_reason(
        output_root,
        process_supervisor,
        meeting_id=str(meeting_id or ""),
        group_id=str(group_id or ""),
    )


def _session_check_payload_with_process_reason(
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
    *,
    meeting_id: str,
    group_id: str,
) -> dict[str, object]:
    groups = session_process_groups_snapshot(process_supervisor)
    session = check_live_agent_session(
        output_root,
        process_supervisor,
        meeting_id=meeting_id,
        group_id=group_id,
        groups=groups,
    )
    resolved_group_id = str(session.get("group_id") or "").strip()
    if not resolved_group_id or "process_reason" in session:
        return session
    group = _find_session_process_group(groups, resolved_group_id)
    reason = live_agent_process_health_reason(group) if group else {}
    if not reason:
        return session
    return {**session, "process_reason": reason}


def _find_session_process_group(
    groups: list[dict[str, object]],
    group_id: str,
) -> dict[str, object]:
    for group in groups:
        if str(group.get("group_id") or "") == group_id:
            return group
    return {}


def session_runs_with_readiness(
    runs: list[dict[str, object]],
    *,
    output_root: Path,
    process_supervisor: LiveAgentProcessSupervisor,
) -> list[dict[str, object]]:
    groups = session_process_groups_snapshot(process_supervisor)
    summary = live_agent_session_readiness_summary(output_root, groups)
    readiness_by_target = session_readiness_by_target(summary)
    return [
        {
            **run,
            "readiness": session_run_readiness_overlay(run, readiness_by_target),
        }
        for run in runs
    ]


def session_readiness_by_target(
    summary: dict[str, object],
) -> dict[tuple[str, str], dict[str, object]]:
    items = summary.get("items") if isinstance(summary.get("items"), list) else []
    return {
        (str(item.get("meeting_id") or ""), str(item.get("group_id") or "")): item
        for item in items
        if isinstance(item, dict)
    }


def session_run_readiness_overlay(
    run: dict[str, object],
    readiness_by_target: dict[tuple[str, str], dict[str, object]],
) -> dict[str, object]:
    meeting_id = safe_health_identity(run.get("meeting_id"))
    group_id = safe_health_identity(run.get("group_id"))
    if not meeting_id or not group_id:
        return {"status": "degraded", "attention": ["session_run:missing_target"]}
    readiness = readiness_by_target.get((meeting_id, group_id))
    if readiness is None:
        return {
            "meeting_id": meeting_id,
            "group_id": group_id,
            "status": "degraded",
            "attention": ["session_run:no_current_readiness"],
        }
    return dict(readiness)


def session_process_groups_snapshot(
    process_supervisor: LiveAgentProcessSupervisor,
) -> list[dict[str, object]]:
    if not hasattr(process_supervisor, "snapshot_groups"):
        return []
    groups = process_supervisor.snapshot_groups()
    return [group for group in groups if isinstance(group, dict)] if isinstance(groups, list) else []
