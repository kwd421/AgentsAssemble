"""Compatibility exports for PostgreSQL application database ownership.

Replacement: ``agentsassemble.persistence.postgres.application_database``.
Removal gate: all direct imports and monkeypatch targets use the replacement
path for one compatibility window.
"""
from agentsassemble.persistence.postgres.application_database import (
    PostgresApplicationDatabase,
    PostgresConnectionProvider,
    SchemaChecker,
)

__all__ = [
    "PostgresApplicationDatabase",
    "PostgresConnectionProvider",
    "SchemaChecker",
]
