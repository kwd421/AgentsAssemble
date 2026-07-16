from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.legacy_room_migration import find_legacy_message_imports, migrate_legacy_messages
from agentsassemble.persistence.local.identity.registry import (
    identity_store_for_output_root,
    reset_identity_store_registry,
)
from agentsassemble.room_store import RoomStore


class LegacyRoomMigrationTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_identity_store_registry()

    def test_prefers_official_live_events_and_preserves_actor_and_time(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("legacy-room", label="Legacy")
            meeting = root / "meetings" / "legacy-room"
            meeting.mkdir(parents=True)
            events = [
                {"id": "status-1", "kind": "status", "content": "working"},
                {
                    "id": "msg-1",
                    "kind": "message",
                    "official_record": True,
                    "role_id": "agent-one",
                    "display_name": "Agent One",
                    "content": "hello room",
                    "created_at": "2026-01-02T03:04:05+00:00",
                    "round": "round_1",
                    "turn_id": "turn-1",
                },
            ]
            (meeting / "live_events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
            )
            (meeting / "transcript.md").write_text("## Round 1\n\n### Wrong\n\nduplicate\n", encoding="utf-8")

            imports = find_legacy_message_imports(root)
            self.assertEqual(len(imports), 1)
            self.assertEqual(imports[0].messages[0].source_id, "msg-1")

            migrate_legacy_messages(root, apply=False)
            result = migrate_legacy_messages(root, apply=True)
            messages = store.read_events("legacy-room", event_types=("message_final",))
            self.assertEqual(result["imported_message_count"], 1)
            self.assertEqual(messages[0]["content"], "hello room")
            self.assertEqual(messages[0]["created_at"], "2026-01-02T03:04:05+00:00")
            self.assertEqual(messages[0]["actor"]["participant_id"], "agent-one")
            self.assertEqual(store.participant("legacy-room", "agent-one")["status"], "left")
            self.assertTrue((Path(str(result["backup_dir"])) / "rooms.sqlite3").exists())

    def test_transcript_fallback_is_idempotent_and_dry_run_must_match(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("20260102T030405Z-room", label="Legacy")
            identity_store_for_output_root(root).upsert_room(
                room_id="20260102T030405Z-room", owner_id="host", label="Legacy", origin="test"
            )
            meeting = root / "meetings" / "20260102T030405Z-room"
            meeting.mkdir(parents=True)
            transcript = meeting / "transcript.md"
            transcript.write_text(
                "# Transcript\n\n## Round 1\n\n### Alice\n\nPosition: yes\n\nAlice: final answer\n",
                encoding="utf-8",
            )

            migrate_legacy_messages(root, apply=False)
            transcript.write_text(transcript.read_text(encoding="utf-8") + "\n### Bob\n\nnew\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed"):
                migrate_legacy_messages(root, apply=True)

            migrate_legacy_messages(root, apply=False)
            migrate_legacy_messages(root, apply=True)
            self.assertEqual(store.event_count("20260102T030405Z-room", event_types=("message_final",)), 2)
            self.assertEqual(find_legacy_message_imports(root), [])


if __name__ == "__main__":
    unittest.main()
