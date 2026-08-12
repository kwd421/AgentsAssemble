from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agentsassemble.room.moderation import is_room_member_muted


class RoomModerationTests(unittest.TestCase):
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
