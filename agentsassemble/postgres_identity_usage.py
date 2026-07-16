"""Compatibility exports for PostgreSQL identity usage persistence.

Replacement: ``agentsassemble.persistence.postgres.identity.usage``.
Removal gate: all direct imports use the replacement path for one compatibility
window.
"""
from agentsassemble.persistence.postgres.identity.usage import (
    record_usage,
    usage_summary,
)

__all__ = ["record_usage", "usage_summary"]
