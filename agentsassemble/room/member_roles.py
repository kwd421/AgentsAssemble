from __future__ import annotations

from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.members import set_canonical_room_member_role_in_unit


def update_member_role_in_unit(
    payload: dict[str, object],
    *,
    unit: RoomCommandUnitOfWork,
) -> dict[str, object]:
    """Validate and persist one canonical participant-role command."""

    try:
        return set_canonical_room_member_role_in_unit(
            unit,
            participant_id=payload.get("participant_id"),
            role=payload.get("role"),
        )
    except ValueError as error:
        raise RoomCommandRejected(
            str(error),
            code="invalid_participant_role",
        ) from error


__all__ = ["update_member_role_in_unit"]
