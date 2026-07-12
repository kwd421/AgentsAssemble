from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.legacy_live_agent_session_control import (
    session_check_error_message,
    session_check_operation_status,
    session_check_operation_summary,
    session_ensure_error_message,
    session_ensure_operation_summary,
    session_recover_error_message,
    session_recover_operation_summary,
    session_restart_error_message,
    session_restart_operation_summary,
    session_resume_error_message,
    session_resume_operation_summary,
    session_start_error_details,
    session_start_error_message,
    session_start_operation_status,
    session_start_operation_summary,
    session_stop_error_message,
    session_stop_operation_status,
    session_stop_operation_summary,
)
from agentsassemble.legacy_live_agent_session_projection import (
    session_check_operation_details,
    session_start_operation_details,
    session_stop_operation_details,
)
from agentsassemble.meeting_events import clean_lobby_text


SessionAction = Callable[..., dict[str, object]]
OperationRecorder = Callable[..., object]


@dataclass(frozen=True)
class LegacySessionMutationActions:
    start: SessionAction
    ensure: SessionAction
    resume: SessionAction
    resume_agent: SessionAction
    agent_timing: SessionAction
    agent_options: SessionAction
    check: SessionAction
    restart: SessionAction
    recover: SessionAction
    stop: SessionAction
    stop_agent: SessionAction


class LegacySessionMutationError(ValueError):
    def __init__(self, message: str, *, details: dict[str, object]) -> None:
        super().__init__(message)
        self.details = details


class LegacyLiveAgentSessionMutationService:
    def __init__(
        self,
        output_root: Path,
        *,
        processes: object,
        session_runs: object,
        actions: LegacySessionMutationActions,
        record_operation: OperationRecorder,
    ) -> None:
        self.output_root = output_root
        self.processes = processes
        self.session_runs = session_runs
        self.actions = actions
        self.record_operation = record_operation

    def start(self, payload: dict[str, object], *, default_server: str) -> dict[str, object]:
        return self._group_start_like(
            "start",
            payload,
            lambda: self.actions.start(self.output_root, self.processes, payload, default_server=default_server),
        )

    def ensure(self, payload: dict[str, object], *, default_server: str) -> dict[str, object]:
        return self._group_start_like(
            "ensure",
            payload,
            lambda: self.actions.ensure(self.output_root, self.processes, payload, default_server=default_server),
        )

    def resume(self, payload: dict[str, object], *, default_server: str) -> dict[str, object]:
        return self._group_start_like(
            "resume",
            payload,
            lambda: self.actions.resume(self.output_root, self.processes, payload, default_server=default_server),
        )

    def resume_agent(self, payload: dict[str, object], *, default_server: str) -> dict[str, object]:
        agent_id = clean_lobby_text(payload.get("agent_id"), limit=128)
        try:
            session = self.actions.resume_agent(
                self.output_root,
                self.processes,
                payload,
                default_server=default_server,
            )
        except (OSError, ValueError) as error:
            self._raise_failure("resume_agent", payload, error, agent_id=agent_id)
        details = {**session_start_operation_details(session), "agent_id": str(session.get("agent_id") or agent_id)}
        self._record(
            "resume_agent",
            status=session_start_operation_status(session),
            target_id=str(session.get("agent_id") or agent_id or session.get("meeting_id") or ""),
            summary=session_resume_operation_summary(session),
            details=details,
        )
        return session

    def agent_timing(self, payload: dict[str, object]) -> dict[str, object]:
        return self._agent_setting("agent_timing", payload, self.actions.agent_timing)

    def agent_options(self, payload: dict[str, object]) -> dict[str, object]:
        return self._agent_setting("agent_options", payload, self.actions.agent_options)

    def check(self, payload: dict[str, object]) -> dict[str, object]:
        try:
            session = self.actions.check(self.output_root, self.processes, payload)
        except (OSError, ValueError) as error:
            self._raise_failure("check", payload, error)
        self._record(
            "check",
            status=session_check_operation_status(session),
            target_id=str(session.get("meeting_id") or payload.get("meeting_id") or ""),
            summary=session_check_operation_summary(session),
            details=session_check_operation_details(session),
        )
        return session

    def restart(self, payload: dict[str, object]) -> dict[str, object]:
        return self._group_restart_like("restart", payload, self.actions.restart)

    def recover(self, payload: dict[str, object]) -> dict[str, object]:
        return self._group_restart_like("recover", payload, self.actions.recover)

    def stop(self, payload: dict[str, object]) -> dict[str, object]:
        return self._stop("stop", payload, self.actions.stop)

    def stop_agent(self, payload: dict[str, object]) -> dict[str, object]:
        return self._stop("stop_agent", payload, self.actions.stop_agent, agent_scoped=True)

    def _group_start_like(
        self,
        action: str,
        payload: dict[str, object],
        invoke: Callable[[], dict[str, object]],
    ) -> dict[str, object]:
        try:
            session = invoke()
        except (OSError, ValueError) as error:
            self._raise_failure(action, payload, error)
        summaries = {
            "start": session_start_operation_summary,
            "ensure": session_ensure_operation_summary,
            "resume": session_resume_operation_summary,
        }
        self._record(
            action,
            status=session_start_operation_status(session),
            target_id=str(session.get("meeting_id") or payload.get("meeting_id") or ""),
            summary=summaries[action](session),
            details=session_start_operation_details(session),
        )
        return session

    def _group_restart_like(
        self,
        action: str,
        payload: dict[str, object],
        invoke: SessionAction,
    ) -> dict[str, object]:
        try:
            session = invoke(self.output_root, self.processes, payload)
        except (OSError, ValueError) as error:
            self._raise_failure(action, payload, error)
        summary = session_restart_operation_summary(session) if action == "restart" else session_recover_operation_summary(session)
        self._record(
            action,
            status=session_start_operation_status(session),
            target_id=str(session.get("meeting_id") or payload.get("meeting_id") or ""),
            summary=summary,
            details=session_start_operation_details(session),
        )
        return session

    def _stop(
        self,
        action: str,
        payload: dict[str, object],
        invoke: SessionAction,
        *,
        agent_scoped: bool = False,
    ) -> dict[str, object]:
        agent_id = clean_lobby_text(payload.get("agent_id"), limit=128) if agent_scoped else ""
        try:
            session = invoke(self.output_root, self.processes, payload)
        except (OSError, ValueError) as error:
            self._raise_failure(action, payload, error, agent_id=agent_id)
        stopped_runs = self.session_runs.mark_matching_stopped(
            meeting_id=str(session.get("meeting_id") or payload.get("meeting_id") or ""),
            group_id=str(session.get("group_id") or payload.get("group_id") or ""),
            reason=f"session.{action}",
        )
        if stopped_runs:
            session["session_runs"] = stopped_runs
        details = session_stop_operation_details(session)
        if agent_scoped:
            details = {**details, "agent_id": str(session.get("agent_id") or agent_id)}
        self._record(
            action,
            status=session_stop_operation_status(session),
            target_id=str(session.get("agent_id") or agent_id or session.get("meeting_id") or ""),
            summary=session_stop_operation_summary(session),
            details=details,
        )
        return session

    def _agent_setting(
        self,
        action: str,
        payload: dict[str, object],
        invoke: SessionAction,
    ) -> dict[str, object]:
        agent_id = clean_lobby_text(payload.get("agent_id"), limit=128)
        try:
            session = invoke(self.output_root, payload)
        except (OSError, ValueError) as error:
            self._raise_failure(action, payload, error, agent_id=agent_id, raw_error=True)
        if action == "agent_timing":
            summary = f"poll_interval={session.get('poll_interval')}"
            details = {
                "agent_id": str(session.get("agent_id") or agent_id),
                "poll_interval": session.get("poll_interval"),
                "config_path": str(session.get("config_path") or ""),
            }
        else:
            summary = f"permission_option={session.get('permission_option')} fast_mode={session.get('fast_mode')}"
            details = {
                "agent_id": str(session.get("agent_id") or agent_id),
                "permission_option": session.get("permission_option"),
                "fast_mode": session.get("fast_mode"),
                "config_path": str(session.get("config_path") or ""),
            }
        self._record(action, status="updated", target_id=str(session.get("agent_id") or agent_id), summary=summary, details=details)
        return session

    def _raise_failure(
        self,
        action: str,
        payload: dict[str, object],
        error: Exception,
        *,
        agent_id: str = "",
        raw_error: bool = False,
    ) -> None:
        error_messages = {
            "start": session_start_error_message,
            "ensure": session_ensure_error_message,
            "resume": session_resume_error_message,
            "resume_agent": session_resume_error_message,
            "check": session_check_error_message,
            "restart": session_restart_error_message,
            "recover": session_recover_error_message,
            "stop": session_stop_error_message,
            "stop_agent": session_stop_error_message,
        }
        safe_error = str(error) if raw_error else error_messages[action](error)
        safe_details = session_start_error_details(payload, error)
        if agent_id:
            safe_details = {**safe_details, "agent_id": agent_id}
        target_id = agent_id or str(safe_details.get("meeting_id") or safe_details.get("requested_meeting_id") or "")
        if action == "start":
            target_id = str(safe_details.get("meeting_id") or "")
        self._record(action, status="failed", target_id=target_id, error=safe_error, details=safe_details)
        raise LegacySessionMutationError(safe_error, details=safe_details) from error

    def _record(self, action: str, **fields: object) -> None:
        self.record_operation(self.output_root, operation=f"session.{action}", **fields)
