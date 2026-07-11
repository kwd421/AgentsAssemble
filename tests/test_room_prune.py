from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.identity_store import identity_store_for_output_root, reset_identity_store_registry
from agentsassemble.room_prune import find_empty_rooms, prune_empty_rooms
from agentsassemble.room_store import RoomStore


class EmptyRoomPruneTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_identity_store_registry()

    def test_only_room_created_without_sessions_guests_or_artifacts_is_candidate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("empty", label="Empty")
            store.create_room("talked", label="Talked")
            store.append_event("talked", "message_final", content="hello")
            store.create_room("agent", label="Agent")
            store.upsert_session(
                "agent",
                {"session_id": "session-1", "participant_id": "codex", "status": "attached"},
            )
            store.create_room("artifact", label="Artifact")
            artifact = root / "rooms" / "artifact" / "smoke" / "result.json"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("{}", encoding="utf-8")

            self.assertEqual([item.room_id for item in find_empty_rooms(root)], ["empty"])

    def test_apply_requires_unchanged_dry_run_and_backs_up_both_databases(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("empty", label="Empty")
            identity_store_for_output_root(root).upsert_room(
                room_id="empty", owner_id="operator-local", label="Empty", origin="test"
            )

            dry_run = prune_empty_rooms(root, apply=False)
            applied = prune_empty_rooms(root, apply=True)

            self.assertEqual(dry_run["candidate_room_ids"], ["empty"])
            self.assertEqual(applied["deleted_count"], 1)
            self.assertFalse(store.room("empty"))
            backup_dir = Path(str(applied["backup_dir"]))
            self.assertTrue((backup_dir / "rooms.sqlite3").exists())
            self.assertTrue((backup_dir / "identity.db").exists())

    def test_apply_aborts_when_room_changed_after_dry_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("empty", label="Empty")
            prune_empty_rooms(root, apply=False)
            store.append_event("empty", "message_final", content="now used")

            with self.assertRaisesRegex(ValueError, "changed"):
                prune_empty_rooms(root, apply=True)
            self.assertTrue(store.room("empty"))

    def test_deleted_room_tombstone_blocks_stale_implicit_recreation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RoomStore(Path(temp_dir))
            store.create_room("stale-room", label="Stale")

            self.assertTrue(store.delete_room("stale-room", reason="user_deleted"))
            self.assertTrue(store.room_is_deleted("stale-room"))
            self.assertFalse(store.room("stale-room"))
            with self.assertRaisesRegex(ValueError, "cannot be recreated implicitly"):
                store.create_room("stale-room", label="Stale")


if __name__ == "__main__":
    unittest.main()
