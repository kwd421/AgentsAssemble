"""Explicit compensation for durable admission work that cannot finish."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from agentsassemble.identity_store import IdentityBackend
from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.room_invite_application import InviteApplicationService
from agentsassemble.room_session_service import RoomSessionService


class RoomAdmissionCompensationFailed(RuntimeError):
    """A compensating side effect failed before the saga could finish."""

    def __init__(self, workflow_id: str, cause: Exception) -> None:
        super().__init__(f"admission compensation failed: {type(cause).__name__}")
        self.workflow_id = workflow_id
        self.cause = cause


class RoomAdmissionSaga:
    """Make terminal local admission cleanup durable and retryable."""

    def __init__(
        self,
        *,
        invites: InviteApplicationService,
        sessions: RoomSessionService,
        identities: IdentityBackend,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._invites = invites
        self._sessions = sessions
        self._identities = identities
        self._now = now or (lambda: datetime.now(UTC))

    def compensate_room_unavailable(
        self,
        workflow: dict[str, object],
    ) -> dict[str, object]:
        workflow_id = clean_lobby_text(workflow.get("workflow_id"), limit=128)
        if not workflow_id:
            raise ValueError("admission compensation requires a workflow id")
        previous_phase = (
            clean_lobby_text(workflow.get("status"), limit=64) or "started"
        )
        if previous_phase == "compensating":
            previous_phase = (
                clean_lobby_text(workflow.get("resume_phase"), limit=64) or "started"
            )
        moment = self._now().isoformat()
        current = self._invites.update_admission_workflow(
            workflow_id,
            {
                "status": "compensating",
                "resume_phase": previous_phase,
                "compensation_status": "pending",
                "compensation_failure_code": "",
                "invite_consumption_retained": bool(workflow.get("invite_consumed")),
                "updated_at": moment,
            },
        )
        session_compensated = bool(current.get("session_compensated"))
        membership_compensated = bool(current.get("membership_compensated"))
        try:
            if not session_compensated:
                session_compensated = self._revoke_workflow_session(workflow_id)
                current = self._invites.update_admission_workflow(
                    workflow_id,
                    {
                        "session_compensated": session_compensated,
                        "updated_at": self._now().isoformat(),
                    },
                )
            if not membership_compensated:
                membership_compensated = self._remove_workflow_membership(current)
                current = self._invites.update_admission_workflow(
                    workflow_id,
                    {
                        "membership_compensated": membership_compensated,
                        "updated_at": self._now().isoformat(),
                    },
                )
            return self._invites.update_admission_workflow(
                workflow_id,
                {
                    "status": "failed_terminal",
                    "resume_phase": "compensated",
                    "failure_code": "room_unavailable",
                    "compensation_status": "completed",
                    "compensation_failure_code": "",
                    "session_compensated": session_compensated,
                    "membership_compensated": membership_compensated,
                    "compensated_at": self._now().isoformat(),
                    "updated_at": self._now().isoformat(),
                },
            )
        except Exception as error:
            raise RoomAdmissionCompensationFailed(workflow_id, error) from error

    def record_failure(
        self,
        workflow_id: str,
        cause: Exception,
    ) -> dict[str, object]:
        """Persist retry state after any surrounding transaction has rolled back."""

        current = self._invites.admission_workflow(workflow_id)
        if current is None:
            raise RuntimeError("admission workflow disappeared during compensation")
        return self._invites.update_admission_workflow(
            workflow_id,
            {
                "status": "failed_retryable",
                "resume_phase": "compensating",
                "compensation_status": "failed_retryable",
                "compensation_failure_code": type(cause).__name__,
                "session_compensated": self._workflow_session_absent(workflow_id),
                "membership_compensated": self._workflow_membership_absent(current),
                "updated_at": self._now().isoformat(),
            },
        )

    def _revoke_workflow_session(self, workflow_id: str) -> bool:
        session_token = self._sessions.token_for_request(workflow_id)
        if self._sessions.verify(session_token) is None:
            return True
        self._sessions.revoke(session_token)
        if self._sessions.verify(session_token) is not None:
            raise RuntimeError("admission session compensation did not revoke the bearer")
        return True

    def _workflow_session_absent(self, workflow_id: str) -> bool:
        session_token = self._sessions.token_for_request(workflow_id)
        return self._sessions.verify(session_token) is None

    def _remove_workflow_membership(self, workflow: dict[str, object]) -> bool:
        room_id = clean_lobby_text(workflow.get("room_id"), limit=128)
        participant_id = clean_lobby_text(workflow.get("participant_id"), limit=128)
        if not room_id or not participant_id:
            return True
        if self._identities.get_membership(room_id, participant_id) is None:
            return True
        self._identities.remove_membership(room_id, participant_id)
        if self._identities.get_membership(room_id, participant_id) is not None:
            raise RuntimeError(
                "admission membership compensation did not remove the membership"
            )
        return True

    def _workflow_membership_absent(self, workflow: dict[str, object]) -> bool:
        room_id = clean_lobby_text(workflow.get("room_id"), limit=128)
        participant_id = clean_lobby_text(
            workflow.get("participant_id"),
            limit=128,
        )
        if not room_id or not participant_id:
            return True
        return self._identities.get_membership(room_id, participant_id) is None
