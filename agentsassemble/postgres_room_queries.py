"""Compatibility exports for PostgreSQL room queries.

Replacement: ``agentsassemble.persistence.postgres.room.queries``.
Removal gate: all direct imports use the replacement path for one compatibility
window.
"""
from agentsassemble.persistence.postgres.room.queries import (
    count_events,
    read_active_participants,
    read_command_record,
    read_event_by_id,
    read_event_sequence,
    read_events,
    read_latest_event_sequence,
    read_oldest_event_sequence,
    read_participant,
    read_participants,
    read_room,
    read_room_settings,
    read_rooms,
    read_session,
    read_sessions,
    room_is_deleted,
)

__all__ = [
    "count_events",
    "read_active_participants",
    "read_command_record",
    "read_event_by_id",
    "read_event_sequence",
    "read_events",
    "read_latest_event_sequence",
    "read_oldest_event_sequence",
    "read_participant",
    "read_participants",
    "read_room",
    "read_room_settings",
    "read_rooms",
    "read_session",
    "read_sessions",
    "room_is_deleted",
]
