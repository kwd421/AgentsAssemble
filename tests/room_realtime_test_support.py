from __future__ import annotations

from dataclasses import dataclass

from agentsassemble.admission.invite_service import InviteApplicationService
from agentsassemble.admission.session_service import RoomSessionService
from agentsassemble.persistence.local.admission.repository import (
    MemoryInviteSessionRepository,
)
from agentsassemble.application.public_invite_runtime import PublicInviteRuntime


@dataclass(frozen=True)
class MemoryRoomAccessServices:
    repository: MemoryInviteSessionRepository
    public_invite: PublicInviteRuntime
    invites: InviteApplicationService
    sessions: RoomSessionService

    def controller_kwargs(self) -> dict[str, object]:
        return {
            "invite_application": self.invites,
            "room_sessions": self.sessions,
        }


def memory_room_access_services() -> MemoryRoomAccessServices:
    repository = MemoryInviteSessionRepository()
    public_invite = PublicInviteRuntime(environ={})
    invites = InviteApplicationService(
        repository,
        public_url=public_invite.public_url,
    )
    sessions = RoomSessionService(
        repository,
        token_prefix="aas1",
        ttl_seconds=3600,
        token_key=invites.signing_secret,
    )
    return MemoryRoomAccessServices(
        repository=repository,
        public_invite=public_invite,
        invites=invites,
        sessions=sessions,
    )
