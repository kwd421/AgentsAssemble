"""Compatibility exports for PostgreSQL room repository ownership.

Replacement: ``agentsassemble.persistence.postgres.room.repository``.
Removal gate: all direct imports and monkeypatch targets use the replacement
path for one compatibility window.
"""
from agentsassemble.persistence.postgres.room.repository import (
    PostgresRoomRepository,
)

__all__ = ["PostgresRoomRepository"]
