import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.room_store import RoomStore


class CanonicalRoomEventStoreTests(unittest.TestCase):
    def test_session_fields_can_be_explicitly_cleared_and_command_results_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("general")
            store.upsert_session(
                "general",
                {
                    "session_id": "codex",
                    "participant_id": "codex",
                    "status": "attached",
                    "runtime_status": "busy",
                    "pid": 123,
                    "last_error": "boom",
                },
            )

            updated = store.update_session_fields(
                "general",
                "codex",
                status="detached",
                runtime_status="stopped",
                pid=None,
                last_error="",
                enabled=False,
            )
            first = store.record_command_result(
                "general",
                "req-1",
                {"op": "ack", "request_id": "req-1", "accepted": True},
            )
            duplicate = store.record_command_result(
                "general",
                "req-1",
                {"op": "ack", "request_id": "req-1", "accepted": False},
            )

        self.assertEqual(updated["runtime_status"], "stopped")
        self.assertIsNone(updated["pid"])
        self.assertEqual(updated["last_error"], "")
        self.assertFalse(updated["enabled"])
        self.assertTrue(first["accepted"])
        self.assertEqual(duplicate, first)

    def test_room_store_assigns_monotonic_sequence_across_instances_and_notifies_listener(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_store = RoomStore(root)
            second_store = RoomStore(root)
            received: list[dict[str, object]] = []
            remove_listener = first_store.add_event_listener("general", received.append)

            first = first_store.append_event(
                "general",
                "message_final",
                actor_id="human",
                actor_type="human",
                content="first",
            )
            second = second_store.append_event(
                "general",
                "message_final",
                participant_id="codex",
                actor_type="agent",
                content="second",
            )
            remove_listener()
            third = second_store.append_event("general", "system", content="third")

        self.assertEqual([first["seq"], second["seq"], third["seq"]], [1, 2, 3])
        self.assertEqual([event["id"] for event in received], [first["id"], second["id"]])
        self.assertEqual(first["actor"], {"participant_id": "human", "participant_type": "human"})
        self.assertEqual(second["actor"], {"participant_id": "codex", "participant_type": "agent"})
        self.assertTrue(all(event["v"] == 1 for event in (first, second, third)))

    def test_canonicalize_events_migrates_general_schema_with_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            events_path = root / "rooms" / "general" / "events.jsonl"
            events_path.parent.mkdir(parents=True)
            legacy_rows = [
                {
                    "event_id": "legacy-user",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "room_id": "general",
                    "actor_id": "human",
                    "actor_type": "user",
                    "kind": "user_message",
                    "content": "hello",
                    "metadata": {},
                },
                {
                    "event_id": "legacy-agent",
                    "created_at": "2026-01-01T00:00:01+00:00",
                    "room_id": "general",
                    "actor_id": "codex",
                    "actor_type": "agent",
                    "kind": "agent_message",
                    "content": "hi",
                    "metadata": {"source_event_id": "legacy-user"},
                },
            ]
            events_path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in legacy_rows),
                encoding="utf-8",
            )

            report = RoomStore(root).canonicalize_events("general")
            migrated = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
            backup = events_path.with_name("events.pre-unification.jsonl")
            backup_exists = backup.exists()

        self.assertTrue(report["migrated"])
        self.assertEqual(report["event_count"], 2)
        self.assertTrue(backup_exists)
        self.assertEqual([event["id"] for event in migrated], ["legacy-user", "legacy-agent"])
        self.assertEqual([event["seq"] for event in migrated], [1, 2])
        self.assertEqual([event["type"] for event in migrated], ["message_final", "message_final"])
        self.assertEqual(migrated[0]["actor"]["participant_type"], "human")
        self.assertEqual(migrated[1]["actor"]["participant_type"], "agent")
        self.assertTrue(all("event_id" not in event and "kind" not in event for event in migrated))

if __name__ == "__main__":
    unittest.main()
