"""Compatibility exports for provider turn-context assembly."""

from agentsassemble.room.turn_context import (
    BoundedProviderContext,
    DEFAULT_ROOM_TURN_MAX_PROMPT_CHARS,
    DEFAULT_ROOM_TURN_MAX_RECENT_EVENTS,
    MAX_MODEL_VISIBLE_MEDIA_SIZE,
    MODEL_VISIBLE_MEDIA_REPRESENTATIONS,
    UNSUPPORTED_MEDIA_AUDIT_NOTE,
    _agent_turn_prompt,
    _bound_room_turn_packet,
    _nonnegative_int,
    build_provider_bootstrap_input,
    build_provider_recovery_input,
    build_provider_turn_input,
    build_room_turn_packet,
    room_memory_from_session,
)


__all__ = [
    "BoundedProviderContext",
    "DEFAULT_ROOM_TURN_MAX_PROMPT_CHARS",
    "DEFAULT_ROOM_TURN_MAX_RECENT_EVENTS",
    "MAX_MODEL_VISIBLE_MEDIA_SIZE",
    "MODEL_VISIBLE_MEDIA_REPRESENTATIONS",
    "UNSUPPORTED_MEDIA_AUDIT_NOTE",
    "_agent_turn_prompt",
    "_bound_room_turn_packet",
    "_nonnegative_int",
    "build_provider_bootstrap_input",
    "build_provider_recovery_input",
    "build_provider_turn_input",
    "build_room_turn_packet",
    "room_memory_from_session",
]
