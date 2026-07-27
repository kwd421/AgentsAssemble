"""Canonical room-global settings mutation through the room command boundary."""

from __future__ import annotations

from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.global_settings import ROOM_GLOBAL_SETTING_FIELDS
from agentsassemble.room.text import clean_room_text


class RoomGlobalSettingsCommandService:
    """Validate and persist one partial room-global settings update."""

    def update_in_unit(
        self,
        payload: dict[str, object],
        *,
        actor_id: str,
        actor_type: str,
        unit: RoomCommandUnitOfWork,
    ) -> dict[str, object]:
        unknown = set(payload) - ROOM_GLOBAL_SETTING_FIELDS
        if unknown:
            raise RoomCommandRejected(
                f"Unsupported room settings fields: {', '.join(sorted(unknown))}.",
                code="bad_request",
            )
        if not payload:
            raise RoomCommandRejected(
                "At least one room-global setting is required.",
                code="bad_request",
            )
        try:
            settings = unit.update_room_settings(payload)
        except ValueError as error:
            raise RoomCommandRejected(str(error), code="bad_request") from error
        event = unit.append_event(
            "room_settings_updated",
            actor_id=clean_room_text(actor_id, limit=128),
            actor_type=clean_room_text(actor_type, limit=32) or "human",
            room_settings=settings,
        )
        return {
            "room_settings": settings,
            "event": event,
        }


__all__ = ["RoomGlobalSettingsCommandService"]
