"""Compatibility exports for agentsassemble.legacy.room.preferences_migration."""

from agentsassemble.legacy.room.preferences_migration import (
    LegacyRoomPreferencesMigrationError,
    MIGRATION_TABLE,
    MIGRATION_VERSION,
    PLAN_FILENAME,
    migrate_legacy_room_preferences,
)

__all__ = [
    'LegacyRoomPreferencesMigrationError',
    'MIGRATION_TABLE',
    'MIGRATION_VERSION',
    'PLAN_FILENAME',
    'migrate_legacy_room_preferences',
]
