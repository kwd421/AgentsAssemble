from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import agentsassemble.room_store as compatibility_repository
from agentsassemble.persistence.local.room import repository as owned_repository


class LocalRoomPersistencePackageTests(unittest.TestCase):
    def test_compatibility_and_owned_paths_share_one_sqlite_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            compatibility_store = compatibility_repository.RoomStore(output_root)
            compatibility_store.create_room("general", label="General")
            appended = compatibility_store.append_event(
                "general",
                "message_final",
                content="package move",
            )

            owned_store = owned_repository.RoomStore(output_root)
            events = owned_store.read_events("general")

        self.assertEqual(events[-1]["id"], appended["id"])
        self.assertEqual(events[-1]["content"], "package move")


if __name__ == "__main__":
    unittest.main()
