from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.legacy_live_agent_session_control import (
    session_ensure_error_message,
    session_start_error_details,
    session_start_operation_status,
)
from agentsassemble.legacy_live_agent_session_projection import session_start_operation_details
from agentsassemble.meeting_events import clean_lobby_text


class LegacySessionRunMutationError(ValueError):
    def __init__(self, message: str, *, details: dict[str, object]) -> None:
        super().__init__(message)
        self.details = details


@dataclass(frozen=True)
class LegacySessionRunActions:
    should_reconcile: Callable[..., bool]
    reconcile: Callable[..., list[dict[str, object]]]
    assert_launch_approved: Callable[..., None]
    ensure: Callable[..., dict[str, object]]


class LegacyLiveAgentSessionRunMutationService:
    def __init__(
        self,
        output_root: Path,
        *,
        session_runs: object,
        actions: LegacySessionRunActions,
        record_operation: Callable[..., object],
    ) -> None:
        self.output_root = output_root
        self.session_runs = session_runs
        self.actions = actions
        self.record_operation = record_operation

    def mutate(self, action: str, payload: dict[str, object], *, path_run_id: str = "") -> dict[str, object]:
        if action not in {"pause", "resume", "stop"}:
            raise ValueError(f"Unsupported session-run action: {action}")
        run_id = clean_lobby_text(path_run_id or payload.get("run_id"), limit=128)
        target = _target_details(payload)
        try:
            if not run_id:
                current = self._latest_for_target(target)
                run_id = str(current.get("run_id") or "")
            if action == "pause":
                session_run = self.session_runs.pause_run(run_id)
                response_status = "paused"
            elif action == "resume":
                session_run = self.session_runs.resume_run(run_id)
                response_status = "resumed"
            else:
                session_run = self.session_runs.stop_run(run_id, reason="operator_stop")
                response_status = "stopped"
        except (OSError, ValueError) as error:
            safe_error = session_ensure_error_message(error)
            details = {"session_run_id": run_id, **{key: value for key, value in target.items() if value}}
            self._record(
                action,
                status="failed",
                target_id=run_id or target["meeting_id"],
                error=safe_error,
                details=details,
            )
            raise LegacySessionRunMutationError(safe_error, details=details) from error
        details = {
            "session_run_id": str(session_run.get("run_id") or run_id),
            "meeting_id": str(session_run.get("meeting_id") or ""),
            "group_id": str(session_run.get("group_id") or ""),
            "run_status": str(session_run.get("status") or ""),
            "phase": str(session_run.get("phase") or ""),
        }
        if action == "pause":
            details["paused_status"] = str(session_run.get("paused_status") or "")
        self._record(
            action,
            status="success",
            target_id=str(session_run.get("run_id") or run_id),
            summary=f"{response_status} durable live-agent session run",
            details=details,
        )
        return {"status": response_status, "session_run": session_run}

    def retry_now(
        self,
        payload: dict[str, object],
        *,
        path_run_id: str = "",
        default_server: str = "",
    ) -> dict[str, object]:
        run_id = clean_lobby_text(path_run_id or payload.get("run_id"), limit=128)
        target = _target_details(payload)
        try:
            current_run = self.session_runs.get_run(run_id) if run_id else self._latest_for_target(target)
            run_id = str(current_run.get("run_id") or run_id)
            if not self.actions.should_reconcile(current_run, target_run_id=run_id):
                self._record(
                    "retry_now",
                    status="success",
                    target_id=run_id,
                    summary="skipped durable live-agent session-run retry because it is already ready",
                    details={
                        **_run_details(current_run),
                        "reconciled": False,
                        "result_count": 0,
                        "skipped_reason": "already_ready",
                    },
                )
                return {"status": "skipped", "session_run": current_run, "results": []}
            scheduled_run = self.session_runs.retry_run_now(run_id)
            results = self.actions.reconcile(
                default_server=default_server,
                target_run_id=str(scheduled_run.get("run_id") or run_id),
                approve_real_providers=_payload_bool(payload.get("approve_real_providers")),
            )
        except (OSError, ValueError) as error:
            safe_error = session_ensure_error_message(error)
            details = {"session_run_id": run_id, **{key: value for key, value in target.items() if value}}
            self._record(
                "retry_now",
                status="failed",
                target_id=run_id or target["meeting_id"],
                error=safe_error,
                details=details,
            )
            raise LegacySessionRunMutationError(safe_error, details=details) from error
        session_run = results[-1] if results else scheduled_run
        reconciled = bool(results)
        self._record(
            "retry_now",
            status=_retry_operation_status(session_run, reconciled=reconciled),
            target_id=str(session_run.get("run_id") or run_id),
            summary="scheduled immediate durable live-agent session-run retry",
            details={
                **_run_details(session_run),
                "reconciled": reconciled,
                "result_count": len(results),
            },
        )
        return {
            "status": "reconciled" if reconciled else "scheduled",
            "session_run": session_run,
            "results": results,
        }

    def ensure(self, payload: dict[str, object], *, default_server: str = "") -> dict[str, object]:
        session_run = self.session_runs.begin_run(action="ensure", payload=dict(payload))
        try:
            self.actions.assert_launch_approved(payload, default_server=default_server)
            session = self.actions.ensure(payload, default_server=default_server)
        except (OSError, ValueError) as error:
            safe_error = session_ensure_error_message(error)
            failed_run = self.session_runs.fail_run(session_run["run_id"], safe_error)
            details = session_start_error_details(payload, error)
            details["session_run_id"] = str(failed_run.get("run_id") or "")
            self._record(
                "ensure",
                status="failed",
                target_id=str(details.get("meeting_id") or details.get("requested_meeting_id") or ""),
                error=safe_error,
                details=details,
            )
            raise LegacySessionRunMutationError(safe_error, details=details) from error
        finished_run = self.session_runs.finish_run(session_run["run_id"], session=session)
        response = dict(session)
        response["session_run"] = finished_run
        self._record(
            "ensure",
            status=session_start_operation_status(session),
            target_id=str(session.get("meeting_id") or payload.get("meeting_id") or ""),
            summary="ensured durable live-agent session run",
            details={
                **session_start_operation_details(session),
                "session_run_id": str(finished_run.get("run_id") or ""),
            },
        )
        return response

    def _latest_for_target(self, target: dict[str, str]) -> dict[str, object]:
        if not target["meeting_id"] or not target["group_id"]:
            raise ValueError("Missing session run id")
        runs = self.session_runs.list_runs(limit=1, meeting_id=target["meeting_id"], group_id=target["group_id"])
        if not runs:
            raise ValueError("No matching live-agent session run for meeting group target.")
        return runs[-1]

    def _record(self, action: str, **fields: object) -> None:
        self.record_operation(self.output_root, operation=f"session_run.{action}", **fields)


def _target_details(payload: dict[str, object]) -> dict[str, str]:
    return {
        "meeting_id": clean_lobby_text(payload.get("meeting_id"), limit=128),
        "group_id": clean_lobby_text(payload.get("group_id"), limit=128),
    }


def _run_details(session_run: dict[str, object]) -> dict[str, object]:
    return {
        "session_run_id": str(session_run.get("run_id") or ""),
        "meeting_id": str(session_run.get("meeting_id") or ""),
        "group_id": str(session_run.get("group_id") or ""),
        "run_status": str(session_run.get("status") or ""),
        "phase": str(session_run.get("phase") or ""),
    }


def _retry_operation_status(session_run: dict[str, object], *, reconciled: bool) -> str:
    if not reconciled:
        return "success"
    status = str(session_run.get("status") or "unknown").strip() or "unknown"
    if status in {"failed", "stopped"}:
        return "failed"
    return "success" if status == "ready" else "degraded"


def _payload_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False
