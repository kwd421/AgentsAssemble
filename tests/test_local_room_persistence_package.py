from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import agentsassemble.room_database as compatibility_database
import agentsassemble.room_store as compatibility_repository
import agentsassemble.sqlite_attention_repository as compatibility_attention
from agentsassemble.persistence.local.room import attention as owned_attention
from agentsassemble.persistence.local.room import database as owned_database
from agentsassemble.persistence.local.room import repository as owned_repository


class LocalRoomPersistencePackageTests(unittest.TestCase):
    def test_root_modules_are_explicit_compatibility_exports(self) -> None:
        self.assertIs(
            compatibility_repository.RoomStore,
            owned_repository.RoomStore,
        )
        self.assertIs(
            compatibility_database.open_room_database,
            owned_database.open_room_database,
        )
        self.assertIs(
            compatibility_attention.read_attention_state,
            owned_attention.read_attention_state,
        )

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
