from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agentsassemble.legacy_live_agent_session_control import session_ensure_error_message
from agentsassemble.meeting_events import clean_lobby_text


class LegacySessionRunMutationError(ValueError):
    def __init__(self, message: str, *, details: dict[str, object]) -> None:
        super().__init__(message)
        self.details = details


class LegacyLiveAgentSessionRunMutationService:
    def __init__(self, output_root: Path, *, session_runs: object, record_operation: Callable[..., object]) -> None:
        self.output_root = output_root
        self.session_runs = session_runs
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
