"""Canonical room-global settings mutation through the room command boundary."""

from __future__ import annotations

from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.global_settings import (
    ROOM_GLOBAL_SETTING_FIELDS,
    public_room_global_settings,
    room_settings_revision,
)
from agentsassemble.room.text import clean_room_text

_EXPECTED_REVISION_FIELD = "expected_revision"


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
        unknown = set(payload) - ROOM_GLOBAL_SETTING_FIELDS - {_EXPECTED_REVISION_FIELD}
        if unknown:
            raise RoomCommandRejected(
                f"Unsupported room settings fields: {', '.join(sorted(unknown))}.",
                code="bad_request",
            )
        updates = {
            field: value
            for field, value in payload.items()
            if field in ROOM_GLOBAL_SETTING_FIELDS
        }
        if not updates:
            raise RoomCommandRejected(
                "At least one room-global setting is required.",
                code="bad_request",
            )
        expected_revision = clean_room_text(
            payload.get(_EXPECTED_REVISION_FIELD),
            limit=96,
        )
        if not expected_revision:
            raise RoomCommandRejected(
                "expected_revision is required for room settings updates.",
                code="settings_revision_required",
            )
        current = unit.room_settings()
        current_revision = room_settings_revision(current)
        if expected_revision != current_revision:
            raise RoomCommandRejected(
                "Room settings changed before this update was applied.",
                code="settings_conflict",
            )
        try:
            settings = unit.update_room_settings(updates)
        except ValueError as error:
            raise RoomCommandRejected(str(error), code="bad_request") from error
        public_settings = public_room_global_settings(settings)
        event = unit.append_event(
            "room_settings_updated",
            actor_id=clean_room_text(actor_id, limit=128),
            actor_type=clean_room_text(actor_type, limit=32) or "human",
            room_settings=public_settings,
        )
        return {
            "room_settings": public_settings,
            "event": event,
        }


__all__ = ["RoomGlobalSettingsCommandService"]
