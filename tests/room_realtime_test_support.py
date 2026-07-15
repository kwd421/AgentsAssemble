from __future__ import annotations

from dataclasses import dataclass

from agentsassemble.room_invite_application import InviteApplicationService
from agentsassemble.room_invite_repository import MemoryInviteSessionRepository
from agentsassemble.room_session_service import RoomSessionService


@dataclass(frozen=True)
class MemoryRoomAccessServices:
    repository: MemoryInviteSessionRepository
    invites: InviteApplicationService
    sessions: RoomSessionService

    def controller_kwargs(self) -> dict[str, object]:
        return {
            "invite_application": self.invites,
            "room_sessions": self.sessions,
        }


def memory_room_access_services() -> MemoryRoomAccessServices:
    repository = MemoryInviteSessionRepository()
    invites = InviteApplicationService(repository)
    sessions = RoomSessionService(
        repository,
        token_prefix="aas1",
        ttl_seconds=3600,
        token_key=invites.signing_secret,
    )
    return MemoryRoomAccessServices(
        repository=repository,
        invites=invites,
        sessions=sessions,
    )
