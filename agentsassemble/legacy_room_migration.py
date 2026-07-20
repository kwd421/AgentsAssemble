"""Compatibility exports for agentsassemble.legacy.room.migration."""

from agentsassemble.legacy.room.migration import (
    LegacyMessage,
    LegacyRoomImport,
    MIGRATION_VERSION,
    PLAN_FILENAME,
    find_legacy_message_imports,
    migrate_legacy_messages,
)

__all__ = [
    'LegacyMessage',
    'LegacyRoomImport',
    'MIGRATION_VERSION',
    'PLAN_FILENAME',
    'find_legacy_message_imports',
    'migrate_legacy_messages',
]
