"""Process-local state used only by historical invite compatibility APIs."""

from __future__ import annotations

from agentsassemble.admission.invite_service import InviteApplicationService
from agentsassemble.admission.repository import (
    InviteSessionRepository,
    UnconfiguredInviteSessionRepository,
)
from agentsassemble.application.public_invite_runtime import PublicInviteRuntime
from agentsassemble.persistence.local.admission.repository import MemoryInviteSessionRepository


class InviteCompatibilityState:
    """Own the replaceable process-default invite dependencies in one place."""

    def __init__(self) -> None:
        self.repository: InviteSessionRepository = UnconfiguredInviteSessionRepository()
        self.public_invite_runtime = PublicInviteRuntime()
        self.invite_application = self._build_application()

    def configure_repository(self, repository: InviteSessionRepository) -> None:
        if not isinstance(repository, InviteSessionRepository):
            raise TypeError("repository must implement InviteSessionRepository")
        self.repository = repository
        self.invite_application = self._build_application()

    def reset(self) -> None:
        self.public_invite_runtime = PublicInviteRuntime()
        self.repository = MemoryInviteSessionRepository()
        self.invite_application = self._build_application()

    def _build_application(self) -> InviteApplicationService:
        return InviteApplicationService(
            self.repository,
            public_url=self.public_invite_runtime.public_url,
        )


compatibility_invite_state = InviteCompatibilityState()


def configure_compatibility_invite_repository(repository: InviteSessionRepository) -> None:
    compatibility_invite_state.configure_repository(repository)
