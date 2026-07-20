"""Compatibility exports for agentsassemble.legacy.meeting.http.room_composition."""

from agentsassemble.legacy.meeting.http.room_composition import (
    AgentSessionProcessService,
    RoomRouteAdapters,
    add_channel,
    create_agent_session_payload,
    create_room_invite,
    register_room_routes,
    room_members_payload,
    room_status_payload,
)

__all__ = [
    'AgentSessionProcessService',
    'RoomRouteAdapters',
    'add_channel',
    'create_agent_session_payload',
    'create_room_invite',
    'register_room_routes',
    'room_members_payload',
    'room_status_payload',
]
