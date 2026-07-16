"""Compatibility exports for the bounded PostgreSQL connection pool.

Replacement: ``agentsassemble.persistence.postgres.connection_pool``.
Removal gate: all direct imports and monkeypatch targets use the replacement
path for one compatibility window.
"""
from agentsassemble.persistence.postgres.connection_pool import (
    BoundedPostgresConnectionPool,
    PoolFactory,
    PostgresPoolClosed,
    PostgresPoolSettings,
    PostgresPoolStartupError,
    _default_pool_factory,
)

__all__ = [
    "BoundedPostgresConnectionPool",
    "PoolFactory",
    "PostgresPoolClosed",
    "PostgresPoolSettings",
    "PostgresPoolStartupError",
]
