import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.room_database import (
    ATTENTION_SCHEMA_STATEMENTS,
    ROOM_SCHEMA_VERSION,
    RoomDatabaseMigrationError,
    initialize_room_database,
    open_room_database,
)
from agentsassemble.room_store import RoomStore


class CanonicalRoomEventStoreTests(unittest.TestCase):
    def test_version_one_database_migrates_command_scope_and_attention_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("general")
            with open_room_database(store.database_path) as connection:
                for table in (
                    "conversation_obligations",
                    "scheduled_wakeups",
                    "attention_leases",
                    "attention_jobs",
                    "agent_attention_state",
                ):
                    connection.execute(f"DROP TABLE {table}")
                connection.execute("ALTER TABLE command_results RENAME TO command_results_v2")
                connection.execute(
                    """CREATE TABLE command_results (
                           room_id TEXT NOT NULL,
                           request_id TEXT NOT NULL,
                           created_at TEXT NOT NULL,
                           result_json TEXT NOT NULL,
                           PRIMARY KEY (room_id, request_id)
                       )"""
                )
                connection.execute(
                    """INSERT INTO command_results(room_id, request_id, created_at, result_json)
                       VALUES('general', 'legacy-request', '2026-01-01T00:00:00+00:00', '{"accepted":true}')"""
                )
                connection.execute("DROP TABLE command_results_v2")
                connection.execute(
                    "UPDATE schema_meta SET value = '1' WHERE key = 'schema_version'"
                )

            initialize_room_database(store.rooms_root, store.database_path)

            with open_room_database(store.database_path) as connection:
                version = int(
                    connection.execute(
                        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                    ).fetchone()["value"]
                )
                attention_jobs_exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'attention_jobs'"
                ).fetchone() is not None
            command = store.command_record("general", "", "legacy-request")

        self.assertEqual(version, ROOM_SCHEMA_VERSION)
        self.assertTrue(attention_jobs_exists)
        self.assertTrue(command["result"]["accepted"])

    def test_version_two_database_migrates_attention_schema_without_losing_room_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("general", label="General")
            original = store.append_event("general", "message_final", content="preserve me")
            attention_tables = (
                "conversation_obligations",
                "scheduled_wakeups",
                "attention_leases",
                "attention_jobs",
                "agent_attention_state",
            )
            with open_room_database(store.database_path) as connection:
                for table in attention_tables:
                    connection.execute(f"DROP TABLE {table}")
                connection.execute(
                    "UPDATE schema_meta SET value = '2' WHERE key = 'schema_version'"
                )

            initialize_room_database(store.rooms_root, store.database_path)

            with open_room_database(store.database_path) as connection:
                version = int(
                    connection.execute(
                        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                    ).fetchone()["value"]
                )
                tables = {
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
            preserved = store.read_events("general")

        self.assertEqual(version, ROOM_SCHEMA_VERSION)
        self.assertTrue(set(attention_tables).issubset(tables))
        self.assertEqual(preserved[-1]["id"], original["id"])
        self.assertTrue(ATTENTION_SCHEMA_STATEMENTS)

    def test_version_three_database_adds_delete_command_tombstone_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("deleted-room")
            store.delete_room("deleted-room", reason="preserve tombstone")
            with open_room_database(store.database_path) as connection:
                connection.execute("ALTER TABLE deleted_rooms RENAME TO deleted_rooms_v4")
                connection.execute(
                    """CREATE TABLE deleted_rooms (
                           room_id TEXT PRIMARY KEY,
                           deleted_at TEXT NOT NULL,
                           reason TEXT NOT NULL DEFAULT ''
                       )"""
                )
                connection.execute(
                    """INSERT INTO deleted_rooms(room_id, deleted_at, reason)
                       SELECT room_id, deleted_at, reason FROM deleted_rooms_v4"""
                )
                connection.execute("DROP TABLE deleted_rooms_v4")
                connection.execute(
                    "UPDATE schema_meta SET value = '3' WHERE key = 'schema_version'"
                )

            initialize_room_database(store.rooms_root, store.database_path)

            with open_room_database(store.database_path) as connection:
                version = int(
                    connection.execute(
                        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                    ).fetchone()["value"]
                )
                columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(deleted_rooms)").fetchall()
                }
            tombstone = store.deleted_room_record("deleted-room")

        self.assertEqual(version, ROOM_SCHEMA_VERSION)
        self.assertTrue(
            {
                "principal_id",
                "request_id",
                "action",
                "payload_hash",
                "cleanup_status",
                "room_name",
                "result_json",
            }.issubset(columns)
        )
        self.assertEqual(tombstone["reason"], "preserve tombstone")
        self.assertEqual(tombstone["cleanup_status"], "complete")
        self.assertEqual(tombstone["result"], {})

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

    def test_legacy_room_files_migrate_once_to_sqlite_with_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            room_dir = root / "rooms" / "general"
            events_path = room_dir / "events.jsonl"
            room_dir.mkdir(parents=True)
            (room_dir / "room.json").write_text(
                json.dumps({"room_id": "general", "label": "#general", "status": "active"}),
                encoding="utf-8",
            )
            (room_dir / "participants.json").write_text(
                json.dumps({"participants": [{"participant_id": "human", "status": "joined"}]}),
                encoding="utf-8",
            )
            (room_dir / "sessions.json").write_text(
                json.dumps({"sessions": [{"session_id": "codex", "participant_id": "codex", "status": "attached"}]}),
                encoding="utf-8",
            )
            (room_dir / "commands.json").write_text(
                json.dumps(
                    {
                        "commands": [
                            {
                                "request_id": "legacy-request",
                                "created_at": "2026-01-01T00:00:02+00:00",
                                "result": {"accepted": True},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
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

            store = RoomStore(root)
            report = store.canonicalize_events("general")
            migrated = store.read_events("general")
            first_count = store.event_count("general", include_hidden=True)
            second_store = RoomStore(root)
            second_count = second_store.event_count("general", include_hidden=True)
            backup = Path(str(report["backup_path"])) / "general"
            backup_events_exists = (backup / "events.jsonl").is_file()
            backup_participants_exists = (backup / "participants.json").is_file()
            legacy_events_exists = events_path.exists()
            database_exists = store.database_path.is_file()
            migrated_participant = second_store.participant("general", "human")
            migrated_session = second_store.session("general", "codex")
            migrated_command = second_store.command_result("general", "legacy-request")

        self.assertTrue(report["migrated"])
        self.assertEqual(report["event_count"], 2)
        self.assertTrue(backup_events_exists)
        self.assertTrue(backup_participants_exists)
        self.assertFalse(legacy_events_exists)
        self.assertTrue(database_exists)
        self.assertEqual(first_count, second_count)
        self.assertEqual([event["id"] for event in migrated], ["legacy-user", "legacy-agent"])
        self.assertEqual([event["seq"] for event in migrated], [1, 2])
        self.assertEqual([event["type"] for event in migrated], ["message_final", "message_final"])
        self.assertEqual(migrated[0]["actor"]["participant_type"], "human")
        self.assertEqual(migrated[1]["actor"]["participant_type"], "agent")
        self.assertTrue(all("event_id" not in event and "kind" not in event for event in migrated))
        self.assertEqual(migrated_participant["status"], "joined")
        self.assertEqual(migrated_session["status"], "attached")
        self.assertTrue(migrated_command["accepted"])

    def test_legacy_terminal_chrome_is_hidden_but_preserved_for_audit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            room_dir = root / "rooms" / "general"
            room_dir.mkdir(parents=True)
            rows = [
                {
                    "id": "human-message",
                    "seq": 1,
                    "v": 1,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "room_id": "general",
                    "type": "message_final",
                    "actor": {"participant_id": "human", "participant_type": "human"},
                    "content": "hello",
                },
                {
                    "id": "old-tui",
                    "seq": 2,
                    "v": 1,
                    "created_at": "2026-01-01T00:00:01+00:00",
                    "room_id": "general",
                    "type": "message_final",
                    "actor": {"participant_id": "codex", "participant_type": "agent"},
                    "content": "⠋ Working (12s · esc to interrupt)\n› Type /help for commands",
                    "message_source": "terminal_capture",
                },
                {
                    "id": "real-answer",
                    "seq": 3,
                    "v": 1,
                    "created_at": "2026-01-01T00:00:02+00:00",
                    "room_id": "general",
                    "type": "message_final",
                    "actor": {"participant_id": "codex", "participant_type": "agent"},
                    "content": "Working 상태를 점검했어.",
                    "message_source": "structured_message",
                },
            ]
            (room_dir / "events.jsonl").write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )

            store = RoomStore(root)
            visible = store.read_events("general")
            audit = store.read_events("general", include_hidden=True)

        self.assertEqual([event["id"] for event in visible], ["human-message", "real-answer"])
        self.assertEqual([event["id"] for event in audit], ["human-message", "old-tui", "real-answer"])
        self.assertEqual(audit[1]["visibility"], "legacy_hidden")

    def test_event_pages_use_sequence_boundaries_without_full_history_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RoomStore(Path(temp_dir))
            store.create_room("general")
            for index in range(10):
                store.append_event("general", "message_final", content=f"message-{index}")

            newest = store.read_events("general", limit=3, newest=True)
            previous = store.read_events("general", before_seq=int(newest[0]["seq"]), limit=3, newest=True)
            after = store.read_events("general", after_seq=int(newest[-2]["seq"]), limit=10)
            latest_sequence = store.latest_event_sequence("general")
            oldest_sequence = store.oldest_event_sequence("general")

        self.assertEqual([event["content"] for event in newest], ["message-7", "message-8", "message-9"])
        self.assertEqual([event["content"] for event in previous], ["message-4", "message-5", "message-6"])
        self.assertEqual([event["content"] for event in after], ["message-9"])
        self.assertEqual(latest_sequence, 11)
        self.assertEqual(oldest_sequence, 1)

    def test_failed_legacy_migration_keeps_original_files_untouched(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            room_dir = root / "rooms" / "general"
            room_dir.mkdir(parents=True)
            events_path = room_dir / "events.jsonl"
            events_path.write_text('{"event_id":"valid","kind":"system"}\nnot-json\n', encoding="utf-8")

            with self.assertRaises(RoomDatabaseMigrationError):
                RoomStore(root)

            original = events_path.read_text(encoding="utf-8")
            database_exists = (root / "rooms" / "rooms.sqlite3").exists()

        self.assertIn("not-json", original)
        self.assertFalse(database_exists)

if __name__ == "__main__":
    unittest.main()
