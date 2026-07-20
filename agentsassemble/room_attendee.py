"""Compatibility exports for agentsassemble.application.room_attendee."""

from agentsassemble.application.room_attendee import (
    AgentAttendee,
    parse_agent_invite_url,
    read_hidden_invite_url,
    run_attendee_from_cli,
)

__all__ = [
    'AgentAttendee',
    'parse_agent_invite_url',
    'read_hidden_invite_url',
    'run_attendee_from_cli',
]
