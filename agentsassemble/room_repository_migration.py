"""Compatibility exports for agentsassemble.legacy.room.repository_migration."""

from agentsassemble.legacy.room.repository_migration import (
    RoomRepositoryTransferError,
    migrate_sqlite_rooms_to_postgres,
)

__all__ = [
    'RoomRepositoryTransferError',
    'migrate_sqlite_rooms_to_postgres',
]
