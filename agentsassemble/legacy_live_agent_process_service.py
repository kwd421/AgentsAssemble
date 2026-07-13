from __future__ import annotations

import math
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.legacy_live_agent_process_control import (
    process_bulk_offline_operation_details,
    process_offline_operation_details,
    process_recover_error_message,
    process_restart_error_message,
    process_start_error_message,
    process_stop_error_message,
    process_stop_running_error_message,
    process_stop_running_operation_status,
)


ProcessAction = Callable[..., dict[str, object]]


@dataclass(frozen=True)
class LegacyProcessMutationActions:
    start: ProcessAction
    stop_running: ProcessAction
    stop: ProcessAction
    restart: ProcessAction
    recover: ProcessAction


class LegacyProcessMutationError(ValueError):
    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class LegacyLiveAgentProcessMutationService:
    def __init__(self, output_root: Path, *, processes: object, actions: LegacyProcessMutationActions, record_operation: Callable[..., object]) -> None:
        self.output_root = output_root
        self.processes = processes
        self.actions = actions
        self.record_operation = record_operation

    def start(self, payload: dict[str, object], *, default_server: str) -> dict[str, object]:
        group_id = _group_id(payload)
        config = _start_config_details(payload)
        try:
            result = self.actions.start(
                self.processes,
                payload,
                default_server=default_server,
                output_root=self.output_root,
            )
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            safe_error = process_start_error_message(error)
            self._record("start", status="failed", target_id=group_id, error=safe_error, details={"group_id": group_id, **config})
            raise LegacyProcessMutationError(safe_error, details={"group_id": group_id}) from error
        except Exception as error:
            self._record_unexpected("start", group_id, error, details={"group_id": group_id, **config})
            raise
        group = result.get("group") if isinstance(result.get("group"), dict) else {}
        result_group_id = _group_id(payload, group)
        self._record(
            "start",
            status="success",
            target_id=result_group_id,
            summary="started live-agent process group",
            details={"group_id": result_group_id, "group_status": str(group.get("status") or ""), **config},
        )
        return result

    def stop_running(self, _payload: dict[str, object]) -> dict[str, object]:
        try:
            stopped = self.actions.stop_running(self.processes, output_root=self.output_root)
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            safe_error = process_stop_running_error_message(error)
            self._record("stop_running", status="failed", target_id="running-groups", error=safe_error)
            raise LegacyProcessMutationError(safe_error) from error
        except Exception as error:
            self._record_unexpected("stop_running", "running-groups", error)
            raise
        result = stopped.get("result") if isinstance(stopped.get("result"), dict) else {}
        self._record(
            "stop_running",
            status=process_stop_running_operation_status(result),
            target_id="running-groups",
            summary="stopped running live-agent process groups",
            details={
                "stopped_count": _nonnegative_int(result.get("stopped_count"), 0),
                "failed_count": _nonnegative_int(result.get("failed_count"), 0),
                "skipped_count": _nonnegative_int(result.get("skipped_count"), 0),
                "stopped_group_ids": _group_ids(result.get("stopped")),
                "failed_group_ids": _group_ids(result.get("failed")),
                **process_bulk_offline_operation_details(result.get("stopped")),
            },
        )
        return stopped

    def stop(self, group_id: str) -> dict[str, object]:
        return self._group_action("stop", group_id, self.actions.stop)

    def restart(self, group_id: str) -> dict[str, object]:
        return self._group_action("restart", group_id, self.actions.restart)

    def recover(self, group_id: str) -> dict[str, object]:
        return self._group_action("recover", group_id, self.actions.recover)

    def _group_action(self, action: str, group_id: str, invoke: ProcessAction) -> dict[str, object]:
        error_message = {"stop": process_stop_error_message, "restart": process_restart_error_message, "recover": process_recover_error_message}[action]
        try:
            result = invoke(self.processes, group_id, output_root=self.output_root)
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            safe_error = error_message(error)
            self._record(action, status="failed", target_id=group_id, error=safe_error, details={"group_id": group_id})
            raise LegacyProcessMutationError(safe_error, details={"group_id": group_id}) from error
        except Exception as error:
            self._record_unexpected(action, group_id, error, details={"group_id": group_id})
            raise
        group = result.get("group") if isinstance(result.get("group"), dict) else {}
        result_group_id = _group_id({}, group) or group_id
        details = {"group_id": result_group_id, "group_status": str(group.get("status") or "")}
        if action == "stop":
            details.update(process_offline_operation_details(group.get("offline")))
        if action == "recover":
            details["previous_status"] = str(group.get("recovered_from_status") or "")
        self._record(
            action,
            status="success",
            target_id=result_group_id,
            summary=f"{action}ed live-agent process group" if action != "stop" else "stopped live-agent process group",
            details=details,
        )
        return result

    def _record(self, action: str, **fields: object) -> None:
        self.record_operation(self.output_root, operation=f"process.{action}", **fields)

    def _record_unexpected(
        self,
        action: str,
        target_id: str,
        error: Exception,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        self._record(
            action,
            status="failed",
            target_id=target_id,
            error="Unexpected process operation failure.",
            details={
                **(details or {}),
                "failure_phase": action,
                "exception_type": type(error).__name__,
            },
        )


def _group_id(payload: dict[str, object], group: dict[str, object] | None = None) -> str:
    return str((group or {}).get("group_id") or payload.get("group_id") or "").strip()


def _group_ids(records: object) -> list[str]:
    if not isinstance(records, list):
        return []
    return [group_id for item in records if isinstance(item, dict) and (group_id := _group_id({}, item))]


def _start_config_details(payload: dict[str, object]) -> dict[str, object]:
    return {
        "auto_restart": _bool(payload.get("auto_restart")),
        "max_restarts": _nonnegative_int(payload.get("max_restarts"), 0),
        "restart_backoff_seconds": _nonnegative_float(payload.get("restart_backoff_seconds"), 5.0),
        "stale_restart_after_seconds": _nonnegative_float(payload.get("stale_restart_after_seconds"), 0.0),
    }


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}


def _nonnegative_int(value: object, default: int) -> int:
    try:
        return max(0, int(value))
    except (OverflowError, TypeError, ValueError):
        return default


def _nonnegative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, parsed) if math.isfinite(parsed) else default
