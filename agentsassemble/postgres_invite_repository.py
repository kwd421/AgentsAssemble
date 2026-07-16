"""Compatibility export for PostgreSQL invite/session repository ownership.

Replacement: ``agentsassemble.persistence.postgres.admission.repository``.
Removal gate: all direct imports use the replacement path for one compatibility
window.
"""
from agentsassemble.persistence.postgres.admission.repository import (
    PostgresInviteSessionRepository,
)

__all__ = ["PostgresInviteSessionRepository"]
