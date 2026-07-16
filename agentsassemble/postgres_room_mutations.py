"""Compatibility exports for PostgreSQL room mutations.

Replacement: ``agentsassemble.persistence.postgres.room.mutations``.
Removal gate: all direct imports use the replacement path for one compatibility
window.
"""
from agentsassemble.persistence.postgres.room.mutations import (
    append_event,
    create_room,
    detach_participant_sessions,
    record_command_result,
    update_participant,
    update_room_settings,
    update_room_status,
    update_session,
    upsert_participant,
    upsert_session,
    write_participant,
    write_session,
)

__all__ = [
    "append_event",
    "create_room",
    "detach_participant_sessions",
    "record_command_result",
    "update_participant",
    "update_room_settings",
    "update_room_status",
    "update_session",
    "upsert_participant",
    "upsert_session",
    "write_participant",
    "write_session",
]
