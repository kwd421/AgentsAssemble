"""Canonical room membership moderation backed by the identity authority."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from agentsassemble.persistence.local.identity.registry import (
    identity_store_for_output_root,
)


def set_room_member_muted(
    output_root: Path,
    *,
    meeting_id: str,
    participant_id: str,
    muted: bool,
) -> dict[str, object]:
    """Toggle a member's muted flag in the identity membership store."""

    return identity_store_for_output_root(output_root).set_membership_muted(
        meeting_id,
        participant_id,
        bool(muted),
    )


def remove_room_member(output_root: Path, meeting_id: str, participant_id: str) -> bool:
    """Delete a saved membership row after the caller handles live cleanup."""

    return identity_store_for_output_root(output_root).remove_membership(
        meeting_id,
        participant_id,
    )


def is_room_member_muted(output_root: Path, meeting_id: str, participant_id: str) -> bool:
    """Return mute state, preserving the existing transient SQLite fail-open policy."""

    try:
        return identity_store_for_output_root(output_root).membership_muted(
            meeting_id,
            participant_id,
        )
    except sqlite3.Error as error:
        logging.getLogger(__name__).warning(
            "mute check failed (treating as not muted): %s",
            error,
        )
        return False


__all__ = [
    "is_room_member_muted",
    "remove_room_member",
    "set_room_member_muted",
]
