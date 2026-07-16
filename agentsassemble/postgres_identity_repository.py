"""Compatibility export for PostgreSQL identity repository ownership.

Replacement: ``agentsassemble.persistence.postgres.identity.repository``.
Removal gate: all direct imports use the replacement path for one compatibility
window.
"""
from agentsassemble.persistence.postgres.identity.repository import (
    PostgresIdentityRepository,
)

__all__ = ["PostgresIdentityRepository"]
