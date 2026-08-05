from __future__ import annotations

from typing import Callable

from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.event_broker import RoomEventBroker
from agentsassemble.room.repository import RoomRepository


AssignPending = Callable[[str, str], bool]
CompatibilityMuteWriter = Callable[[str, str, bool], object]


class RoomMemberMuteService:
    """Persist canonical mute state and synchronize its runtime side effects."""

    def __init__(
        self,
        *,
        store: RoomRepository,
        broker: RoomEventBroker,
        assign_pending: AssignPending,
        compatibility_mute_writer: CompatibilityMuteWriter,
    ) -> None:
        self.store = store
        self.broker = broker
        self._assign_pending = assign_pending
        self._compatibility_mute_writer = compatibility_mute_writer

    def update_in_unit(
        self,
        participant_id: str,
        muted: bool,
        compatibility_member: dict[str, object],
        *,
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        participant = unit.participant(participant_id)
        if not participant:
            raise RoomCommandRejected(
                f"Participant {participant_id} was not found.",
                code="not_found",
            )
        updated = unit.update_participant_fields(
            participant_id,
            muted=muted,
        )
        unit.append_event(
            "participant_muted",
            participant_id=participant_id,
            muted=muted,
        )
        return {
            "participant": updated,
            "member": compatibility_member,
        }

    @staticmethod
    def planned_compatibility_member(
        current: dict[str, object] | None,
        *,
        room_id: str,
        participant_id: str,
        muted: bool,
    ) -> dict[str, object]:
        """Describe the compatibility-roster write before the canonical commit."""

        if current:
            return {**current, "muted": muted}
        return {
            "meeting_id": room_id,
            "participant_id": participant_id,
            "display_name": participant_id,
            "role": "agent",
            "participant_type": "unknown",
            "provider_kind": "",
            "connection_kind": "",
            "status": "",
            "muted": muted,
            "is_host": False,
            "source": "moderation",
            "created_at": "",
            "updated_at": "",
            "last_seen_at": "",
        }

    def apply_after_commit(
        self,
        room_id: str,
        participant_id: str,
        muted: bool,
    ) -> None:
        try:
            self._compatibility_mute_writer(
                room_id,
                participant_id,
                muted,
            )
        except Exception as error:
            raise RoomCommandRejected(
                "The room mute state was saved, but the compatibility roster "
                "did not synchronize; retry the command.",
                code="compatibility_sync_failed",
            ) from error
        participant = self.store.participant(room_id, participant_id)
        session = self.store.session(room_id, participant_id)
        if participant.get("role") != "agent" or not session:
            return
        if muted and session.get("runtime_status") == "busy":
            self.broker.direct_to_bridge(
                room_id,
                participant_id,
                {"op": "agent.control", "action": "interrupt"},
            )
        elif not muted:
            self._assign_pending(room_id, participant_id)


__all__ = [
    "AssignPending",
    "CompatibilityMuteWriter",
    "RoomMemberMuteService",
]
