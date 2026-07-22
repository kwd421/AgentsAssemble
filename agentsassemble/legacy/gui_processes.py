"""Process-control payloads retained by the legacy GUI API."""

from __future__ import annotations

from pathlib import Path

from agentsassemble.legacy.gui_payload import (
    payload_bool,
    payload_nonnegative_float,
    payload_nonnegative_int,
)
from agentsassemble.legacy.live_agent.process_projection import (
    process_payload_with_agent_connection_evidence,
)
from agentsassemble.legacy.live_agent.runtime.operations import (
    append_live_agent_operation,
)
from agentsassemble.legacy.live_agent.runtime.processes import (
    LiveAgentProcessSupervisor,
)


def start_process_payload(
    process_supervisor: LiveAgentProcessSupervisor,
    payload: dict[str, object],
    *,
    default_server: str,
    output_root: Path | None = None,
) -> dict[str, object]:
    config_path = Path(
        str(payload.get("config_path") or "configs/live-agents.example.json"),
    )
    start_kwargs: dict[str, object] = {
        "config_path": config_path,
        "server": str(payload.get("server") or default_server),
        "group_id": str(payload.get("group_id") or "").strip() or None,
        "auto_restart": payload_bool(payload.get("auto_restart")),
        "max_restarts": payload_nonnegative_int(payload.get("max_restarts"), 0),
        "restart_backoff_seconds": payload_nonnegative_float(
            payload.get("restart_backoff_seconds"),
            5.0,
        ),
    }
    stale_restart_after_seconds = payload_nonnegative_float(
        payload.get("stale_restart_after_seconds"),
        0.0,
    )
    if stale_restart_after_seconds > 0:
        start_kwargs["stale_restart_after_seconds"] = stale_restart_after_seconds
    if payload_bool(payload.get("diagnostic")):
        start_kwargs["diagnostic"] = True
    group = process_supervisor.start_group(**start_kwargs)
    response = {"group": group, "groups": process_supervisor.list_groups()}
    return process_payload_with_agent_connection_evidence(response, output_root)


def stop_process_payload(
    process_supervisor: LiveAgentProcessSupervisor,
    group_id: str,
    *,
    output_root: Path | None = None,
) -> dict[str, object]:
    group = process_supervisor.stop_group(group_id)
    response = {"group": group, "groups": process_supervisor.list_groups()}
    return process_payload_with_agent_connection_evidence(response, output_root)


def stop_running_processes_payload(
    process_supervisor: LiveAgentProcessSupervisor,
    *,
    output_root: Path | None = None,
) -> dict[str, object]:
    result = process_supervisor.stop_running_groups()
    response = {"result": result, "groups": process_supervisor.list_groups()}
    return process_payload_with_agent_connection_evidence(response, output_root)


def restart_process_payload(
    process_supervisor: LiveAgentProcessSupervisor,
    group_id: str,
    *,
    output_root: Path | None = None,
) -> dict[str, object]:
    group = process_supervisor.restart_group(group_id)
    response = {"group": group, "groups": process_supervisor.list_groups()}
    return process_payload_with_agent_connection_evidence(response, output_root)


def recover_process_payload(
    process_supervisor: LiveAgentProcessSupervisor,
    group_id: str,
    *,
    output_root: Path | None = None,
) -> dict[str, object]:
    group = process_supervisor.recover_group(group_id)
    response = {"group": group, "groups": process_supervisor.list_groups()}
    return process_payload_with_agent_connection_evidence(response, output_root)


def record_operation(
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
