"""Compatibility exports for local SQLite room database helpers.

Replacement: ``agentsassemble.persistence.local.room.database``.
Removal gate: all direct imports and monkeypatch targets use the replacement
path for one compatibility window.
"""
from agentsassemble.persistence.local.room.database import (
    ATTENTION_SCHEMA_STATEMENTS,
    LEGACY_AUDIT_FILES,
    LEGACY_AUTHORITY_FILES,
    LEGACY_HIDDEN,
    ROOM_DATABASE_FILENAME,
    ROOM_SCHEMA_VERSION,
    VISIBLE,
    RoomDatabaseMigrationError,
    canonical_event_from_record,
    event_visibility,
    initialize_room_database,
    migration_report,
    open_room_database,
)


__all__ = [
    "ATTENTION_SCHEMA_STATEMENTS",
    "LEGACY_AUDIT_FILES",
    "LEGACY_AUTHORITY_FILES",
    "LEGACY_HIDDEN",
    "ROOM_DATABASE_FILENAME",
    "ROOM_SCHEMA_VERSION",
    "VISIBLE",
    "RoomDatabaseMigrationError",
    "canonical_event_from_record",
    "event_visibility",
    "initialize_room_database",
    "migration_report",
    "open_room_database",
]
