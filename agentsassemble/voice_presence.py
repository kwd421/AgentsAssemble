"""Compatibility exports for agentsassemble.room.voice_presence."""

from agentsassemble.room.voice_presence import (
    join_voice,
    leave_all_voice,
    leave_voice,
    reset_voice_presence,
    voice_participants,
    voice_presence_for_room,
)

__all__ = [
    'join_voice',
    'leave_all_voice',
    'leave_voice',
    'reset_voice_presence',
    'voice_participants',
    'voice_presence_for_room',
]
