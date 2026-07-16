from __future__ import annotations

from typing import Callable

from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.errors import RoomCommandRejected


MembershipRemover = Callable[[str, str], object]
ParticipantCleanup = Callable[[str, str], object]
CleanupScheduler = Callable[[float, Callable[[], None]], object]


class RoomParticipantLeaveService:
    """Persist a participant leave and revoke its room access after commit."""

    def __init__(
        self,
        *,
        remove_membership: MembershipRemover,
        leave_all_voice: ParticipantCleanup,
        revoke_participant_sessions: ParticipantCleanup,
        schedule_cleanup: CleanupScheduler,
    ) -> None:
        self._remove_membership = remove_membership
        self._leave_all_voice = leave_all_voice
        self._revoke_participant_sessions = revoke_participant_sessions
        self._schedule_cleanup = schedule_cleanup

    def update_in_unit(
        self,
        participant_id: str,
        *,
        is_owner: bool,
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        participant = unit.participant(participant_id)
        if not participant:
            raise RoomCommandRejected(
                "Participant was not found in this room.",
                code="not_found",
            )
        if is_owner:
            raise RoomCommandRejected(
                "The room owner must transfer ownership or delete the server.",
                code="owner_must_transfer_or_delete",
            )
        updated = unit.update_participant_fields(
            participant_id,
            status="left",
        )
        event = unit.append_event(
            "participant_left",
            participant_id=participant_id,
        )
        return {
            "participant": updated,
            "event": event,
            "revocation_scheduled": True,
        }

    def apply_after_commit(
        self,
        room_id: str,
        participant_id: str,
    ) -> None:
        self._remove_membership(room_id, participant_id)
        self._leave_all_voice(room_id, participant_id)

        def revoke_sessions() -> None:
            self._revoke_participant_sessions(room_id, participant_id)

        self._schedule_cleanup(0.1, revoke_sessions)


__all__ = [
    "CleanupScheduler",
    "MembershipRemover",
    "ParticipantCleanup",
    "RoomParticipantLeaveService",
]
