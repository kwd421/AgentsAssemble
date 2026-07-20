"""Compatibility exports for agentsassemble.legacy.room.settings_migration."""

from agentsassemble.legacy.room.settings_migration import (
    LegacyRoomSettingsMigrationError,
    MIGRATION_META_KEY,
    MIGRATION_VERSION,
    PLAN_FILENAME,
    migrate_legacy_room_settings,
)

__all__ = [
    'LegacyRoomSettingsMigrationError',
    'MIGRATION_META_KEY',
    'MIGRATION_VERSION',
    'PLAN_FILENAME',
    'migrate_legacy_room_settings',
]
