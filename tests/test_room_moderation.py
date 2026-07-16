from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agentsassemble.room.moderation import (
    is_room_member_muted,
    remove_room_member,
    set_room_member_muted,
)


class RoomModerationTests(unittest.TestCase):
    def test_mutation_helpers_delegate_to_identity_membership_authority(self) -> None:
        store = Mock()
        store.set_membership_muted.return_value = {"muted": True}
        store.remove_membership.return_value = True

        with patch(
            "agentsassemble.room.moderation.identity_store_for_output_root",
            return_value=store,
        ):
            muted = set_room_member_muted(
                Path("/tmp/room-moderation"),
                meeting_id="general",
                participant_id="guest",
                muted=True,
            )
            removed = remove_room_member(
                Path("/tmp/room-moderation"),
                "general",
                "guest",
            )

        self.assertEqual(muted, {"muted": True})
        self.assertTrue(removed)
        store.set_membership_muted.assert_called_once_with("general", "guest", True)
        store.remove_membership.assert_called_once_with("general", "guest")

    def test_mute_read_preserves_transient_sqlite_fail_open_policy(self) -> None:
        store = Mock()
        store.membership_muted.side_effect = sqlite3.OperationalError("database is busy")

        with (
            patch(
                "agentsassemble.room.moderation.identity_store_for_output_root",
                return_value=store,
            ),
            self.assertLogs("agentsassemble.room.moderation", level="WARNING"),
        ):
            muted = is_room_member_muted(
                Path("/tmp/room-moderation"),
                "general",
                "guest",
            )

        self.assertFalse(muted)


if __name__ == "__main__":
    unittest.main()
