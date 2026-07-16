"""Compatibility exports for the canonical room repository contract."""

from agentsassemble.room.repository import (
    CommandRecord,
    EventListener,
    EventRecord,
    ParticipantRecord,
    RoomRecord,
    RoomRepository,
    RoomTransaction,
    SessionRecord,
)


__all__ = [
    "CommandRecord",
    "EventListener",
    "EventRecord",
    "ParticipantRecord",
    "RoomRecord",
    "RoomRepository",
    "RoomTransaction",
    "SessionRecord",
]
