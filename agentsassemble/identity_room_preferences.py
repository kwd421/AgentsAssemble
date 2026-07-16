"""Compatibility exports for identity-owned room preferences."""
from agentsassemble.identity.preferences import canonical_user_id
from agentsassemble.persistence.local.identity.preferences import (
    ROOM_PREFERENCE_MIGRATIONS_TABLE,
    ROOM_PREFERENCES_SCHEMA,
    delete_room_preferences,
    encode_room_preferences,
    ensure_room_preferences_schema,
    read_room_preferences,
    update_room_preferences,
)

__all__ = [
    "ROOM_PREFERENCE_MIGRATIONS_TABLE",
    "ROOM_PREFERENCES_SCHEMA",
    "canonical_user_id",
    "delete_room_preferences",
    "encode_room_preferences",
    "ensure_room_preferences_schema",
    "read_room_preferences",
    "update_room_preferences",
]
