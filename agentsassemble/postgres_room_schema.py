"""Compatibility exports for the shared PostgreSQL schema boundary.

Replacement: ``agentsassemble.persistence.postgres.schema``.
Removal gate: all direct imports and monkeypatch targets use the replacement
path for one compatibility window.
"""
from agentsassemble.persistence.postgres.schema import (
    POSTGRES_ROOM_AUTHORITY_ID,
    POSTGRES_ROOM_REQUIRED_TABLES,
    POSTGRES_ROOM_SCHEMA_REVISION,
    PostgresRoomMigrationError,
    PostgresRoomSchemaNotReady,
    _sqlalchemy_psycopg_url,
    require_postgres_room_schema,
    upgrade_postgres_room_schema,
)

__all__ = [
    "POSTGRES_ROOM_AUTHORITY_ID",
    "POSTGRES_ROOM_REQUIRED_TABLES",
    "POSTGRES_ROOM_SCHEMA_REVISION",
    "PostgresRoomMigrationError",
    "PostgresRoomSchemaNotReady",
    "require_postgres_room_schema",
    "upgrade_postgres_room_schema",
]
