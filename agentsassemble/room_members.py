"""Compatibility exports for agentsassemble.room.members."""

from agentsassemble.room.members import (
    ROOM_MEMBERS_FILE,
    ROOM_MEMBER_ROLES,
    ROOM_MEMBER_ROLE_OPTIONS,
    mark_thinking,
    normalize_room_member_role,
    read_room_members,
    room_members_payload,
    thinking_participants,
    upsert_room_member,
)

__all__ = [
    'ROOM_MEMBERS_FILE',
    'ROOM_MEMBER_ROLES',
    'ROOM_MEMBER_ROLE_OPTIONS',
    'mark_thinking',
    'normalize_room_member_role',
    'read_room_members',
    'room_members_payload',
    'thinking_participants',
    'upsert_room_member',
]
