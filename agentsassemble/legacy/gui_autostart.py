"""Retained live-agent process autostart behavior for the GUI."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agentsassemble.legacy.live_agent.runtime.processes import LiveAgentProcessSupervisor


def autostart_live_agent_group(
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
    record_operation: Callable[..., object],
) -> None:
    try:
        group = process_supervisor.start_group(
            config_path=config_path,
            server=server_url,
            group_id=group_id.strip() or None,
            auto_restart=auto_restart,
            max_restarts=max_restarts,
            restart_backoff_seconds=restart_backoff_seconds,
            stale_restart_after_seconds=stale_restart_after_seconds,
        )
    except Exception as error:
        record_operation(
            output_root,
            operation="process.autostart",
            status="failed",
            target_id=group_id,
            error=str(error),
            details={
                "group_id": group_id,
                "auto_restart": bool(auto_restart),
                "max_restarts": max_restarts,
                "restart_backoff_seconds": restart_backoff_seconds,
                "stale_restart_after_seconds": stale_restart_after_seconds,
            },
        )
        print("Live-agent autostart failed; inspect recent operations for details.")
        return
    record_operation(
        output_root,
        operation="process.autostart",
        status="success",
        target_id=str(group.get("group_id") or group_id),
        summary="autostarted live-agent process group",
        details={
            "group_id": str(group.get("group_id") or group_id),
            "group_status": str(group.get("status") or ""),
            "auto_restart": bool(auto_restart),
            "max_restarts": max_restarts,
            "restart_backoff_seconds": restart_backoff_seconds,
            "stale_restart_after_seconds": stale_restart_after_seconds,
        },
    )
