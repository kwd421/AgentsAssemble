"""Compatibility exports for PostgreSQL identity roster persistence.

Replacement: ``agentsassemble.persistence.postgres.identity.roster``.
Removal gate: all direct imports use the replacement path for one compatibility
window.
"""
from agentsassemble.persistence.postgres.identity.roster import (
    count_memberships,
    delete_room,
    get_membership,
    get_room,
    list_memberships,
    list_rooms,
    membership_from_row,
    remove_membership,
    room_from_row,
    set_membership_muted,
    set_room_archived,
    touch_room,
    upsert_membership,
    upsert_room,
)

__all__ = [
    "count_memberships",
    "delete_room",
    "get_membership",
    "get_room",
    "list_memberships",
    "list_rooms",
    "membership_from_row",
    "remove_membership",
    "room_from_row",
    "set_membership_muted",
    "set_room_archived",
    "touch_room",
    "upsert_membership",
    "upsert_room",
]
