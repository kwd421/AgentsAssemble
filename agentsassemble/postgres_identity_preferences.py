"""Compatibility exports for PostgreSQL identity room preferences.

Replacement: ``agentsassemble.persistence.postgres.identity.preferences``.
Removal gate: all direct imports use the replacement path for one compatibility
window.
"""
from agentsassemble.persistence.postgres.identity.preferences import (
    read_room_preferences,
    update_room_preferences,
)

__all__ = ["read_room_preferences", "update_room_preferences"]
