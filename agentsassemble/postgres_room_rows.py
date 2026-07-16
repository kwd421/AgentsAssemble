"""Compatibility export for PostgreSQL room row decoding.

Replacement: ``agentsassemble.persistence.postgres.room.rows``.
Removal gate: all direct imports use the replacement path for one compatibility
window.
"""
from agentsassemble.persistence.postgres.room.rows import payload_from_row

__all__ = ["payload_from_row"]
