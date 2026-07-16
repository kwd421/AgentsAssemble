"""Persistence contracts for room invites and short-lived room sessions."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentsassemble.room_admission_workflow_maintenance import (
    AdmissionWorkflowSelection,
    PurgeReport,
)


class InviteRepositoryError(RuntimeError):
    """Base error for unavailable or invalid invite/session persistence."""


class InviteRepositoryUnavailable(InviteRepositoryError):
    """The configured repository cannot be read."""


class InviteRepositoryCorrupt(InviteRepositoryError):
    """The configured repository contains invalid or unsupported state."""


class InviteRepositoryWriteFailed(InviteRepositoryError):
    """A repository mutation could not be durably persisted."""


class InviteRepositoryNotConfigured(InviteRepositoryError):
    """Invite/session persistence has not been selected for this process."""


@runtime_checkable
class InviteRepository(Protocol):
    def signing_secret(self) -> str: ...

    def existing_signing_secret(self) -> str: ...

    def save_invite(self, record: dict[str, object]) -> None: ...

    def invite(self, invite_id: str) -> dict[str, object] | None: ...

    def invite_for_join_code(self, join_code_fingerprint: str) -> dict[str, object] | None: ...

    def nonce_was_used(self, nonce_fingerprint: str) -> bool: ...

    def consume(
        self,
        *,
        invite_id: str,
        nonce_fingerprint: str,
        reusable: bool,
        max_uses: int,
    ) -> str: ...

    def revoke_invite(self, invite_id: str) -> bool: ...

    def revoke_room_invites(self, room_id: str) -> int: ...

    def list_invites(self) -> list[dict[str, object]]: ...


@runtime_checkable
class SessionRepository(Protocol):
    def save_session(self, token_fingerprint: str, record: dict[str, object]) -> None: ...

    def replace_participant_session(
        self,
        token_fingerprint: str,
        record: dict[str, object],
    ) -> None: ...

    def session(self, token_fingerprint: str) -> dict[str, object] | None: ...

    def revoke_session(self, token_fingerprint: str) -> bool: ...

    def revoke_participant_sessions(self, room_id: str, participant_id: str) -> int: ...

    def revoke_room_sessions(self, room_id: str) -> int: ...

    def list_sessions(self) -> list[tuple[str, dict[str, object]]]: ...


@runtime_checkable
class InviteSessionRepository(InviteRepository, SessionRepository, Protocol):
    def create_admission_workflow(
        self,
        workflow_id: str,
        record: dict[str, object],
    ) -> dict[str, object]: ...

    def admission_workflow(self, workflow_id: str) -> dict[str, object] | None: ...

    def update_admission_workflow(
        self,
        workflow_id: str,
        updates: dict[str, object],
    ) -> dict[str, object]: ...

    def consume_for_admission(
        self,
        workflow_id: str,
        *,
        invite_id: str,
        nonce_fingerprint: str,
        reusable: bool,
        max_uses: int,
        updates: dict[str, object],
    ) -> tuple[str, dict[str, object]]: ...

    def purge_admission_workflows(
        self,
        selection: AdmissionWorkflowSelection,
        *,
        apply: bool,
    ) -> PurgeReport: ...

    def reload(self) -> None: ...

    def clear(self) -> None: ...

    def close(self) -> None: ...


class UnconfiguredInviteSessionRepository:
    """Fail-closed placeholder used until application composition selects storage."""

    _MESSAGE = "Invite/session repository is not configured."

    def _raise(self) -> None:
        raise InviteRepositoryNotConfigured(self._MESSAGE)

    def signing_secret(self) -> str:
        self._raise()

    def existing_signing_secret(self) -> str:
        self._raise()

    def save_invite(self, record: dict[str, object]) -> None:
        del record
        self._raise()

    def invite(self, invite_id: str) -> dict[str, object] | None:
        del invite_id
        self._raise()

    def invite_for_join_code(self, join_code_fingerprint: str) -> dict[str, object] | None:
        del join_code_fingerprint
        self._raise()

    def nonce_was_used(self, nonce_fingerprint: str) -> bool:
        del nonce_fingerprint
        self._raise()

    def consume(
        self,
        *,
        invite_id: str,
        nonce_fingerprint: str,
        reusable: bool,
        max_uses: int,
    ) -> str:
        del invite_id, nonce_fingerprint, reusable, max_uses
        self._raise()

    def revoke_invite(self, invite_id: str) -> bool:
        del invite_id
        self._raise()

    def revoke_room_invites(self, room_id: str) -> int:
        del room_id
        self._raise()

    def list_invites(self) -> list[dict[str, object]]:
        self._raise()

    def save_session(self, token_fingerprint: str, record: dict[str, object]) -> None:
        del token_fingerprint, record
        self._raise()

    def replace_participant_session(
        self,
        token_fingerprint: str,
        record: dict[str, object],
    ) -> None:
        del token_fingerprint, record
        self._raise()

    def session(self, token_fingerprint: str) -> dict[str, object] | None:
        del token_fingerprint
        self._raise()

    def revoke_session(self, token_fingerprint: str) -> bool:
        del token_fingerprint
        self._raise()

    def revoke_participant_sessions(self, room_id: str, participant_id: str) -> int:
        del room_id, participant_id
        self._raise()

    def revoke_room_sessions(self, room_id: str) -> int:
        del room_id
        self._raise()

    def list_sessions(self) -> list[tuple[str, dict[str, object]]]:
        self._raise()

    def create_admission_workflow(
        self,
        workflow_id: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        del workflow_id, record
        self._raise()

    def admission_workflow(self, workflow_id: str) -> dict[str, object] | None:
        del workflow_id
        self._raise()

    def update_admission_workflow(
        self,
        workflow_id: str,
        updates: dict[str, object],
    ) -> dict[str, object]:
        del workflow_id, updates
        self._raise()

    def consume_for_admission(
        self,
        workflow_id: str,
        *,
        invite_id: str,
        nonce_fingerprint: str,
        reusable: bool,
        max_uses: int,
        updates: dict[str, object],
    ) -> tuple[str, dict[str, object]]:
        del workflow_id, invite_id, nonce_fingerprint, reusable, max_uses, updates
        self._raise()

    def purge_admission_workflows(
        self,
        selection: AdmissionWorkflowSelection,
        *,
        apply: bool,
    ) -> PurgeReport:
        del selection, apply
        self._raise()

    def reload(self) -> None:
        self._raise()

    def clear(self) -> None:
        self._raise()

    def close(self) -> None:
        return


__all__ = [
    "InviteRepository",
    "InviteRepositoryCorrupt",
    "InviteRepositoryError",
    "InviteRepositoryNotConfigured",
    "InviteRepositoryUnavailable",
    "InviteRepositoryWriteFailed",
    "InviteSessionRepository",
    "SessionRepository",
    "UnconfiguredInviteSessionRepository",
]
