from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from agentsassemble.persistence.local.room.database import (
    VISIBLE,
    VOTE_BALLOT_INDEX_NAME,
    open_room_database,
)
from agentsassemble.persistence.local.room.repository import _VOTE_BALLOT_EVENTS_QUERY
from agentsassemble.room.repository import RoomRepository
from agentsassemble.room_store import RoomStore
from tests.room_repository_contract import RoomRepositoryContractMixin


class SQLiteRoomRepositoryContractTests(RoomRepositoryContractMixin, unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.repository = RoomStore(Path(self._temporary_directory.name))

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_room_store_implements_repository_protocol(self) -> None:
        self.assertIsInstance(self.repository, RoomRepository)

    def test_ensure_room_rejects_missing_or_invalid_settings(self) -> None:
        self.repository.create_room("missing-settings", label="Missing")
        self.repository.create_room("invalid-settings", label="Invalid")
        self.repository.create_room("invalid-room", label="Invalid room")
        with closing(open_room_database(self.repository.database_path)) as connection:
            connection.execute(
                "DELETE FROM room_settings WHERE room_id = ?",
                ("missing-settings",),
            )
            connection.execute(
                "UPDATE room_settings SET data_json = ? WHERE room_id = ?",
                ("not-json", "invalid-settings"),
            )
            connection.execute(
                "UPDATE rooms SET data_json = ? WHERE room_id = ?",
                ("{}", "invalid-room"),
            )
            connection.execute(
                "DELETE FROM room_settings WHERE room_id = ?",
                ("invalid-room",),
            )

        with self.assertRaisesRegex(ValueError, "settings.*missing"):
            self.repository.ensure_room("missing-settings")
        with self.assertRaises(ValueError):
            self.repository.ensure_room("invalid-settings")
        with self.assertRaisesRegex(ValueError, "record is invalid"):
            self.repository.ensure_room("invalid-room")

        with closing(open_room_database(self.repository.database_path)) as connection:
            settings_count = connection.execute(
                "SELECT COUNT(*) AS count FROM room_settings WHERE room_id = ?",
                ("invalid-room",),
            ).fetchone()["count"]
        self.assertEqual(settings_count, 0)
        self.assertEqual(
            [event["type"] for event in self.repository.read_events("invalid-room")],
            ["room_created"],
        )

    def test_vote_query_uses_poll_and_sequence_index(self) -> None:
        self.repository.create_room("vote-plan")
        poll = self.repository.append_event(
            "vote-plan",
            "message_final",
            participant_id="host-a",
            actor_type="human",
            message_kind="vote",
            vote_question="Choose",
            vote_options=["A", "B"],
        )
        ballot = self.repository.append_event(
            "vote-plan",
            "message_final",
            participant_id="guest-a",
            actor_type="human",
            message_kind="vote_cast",
            vote_id=poll["id"],
            vote_choice="A",
        )

        vote_events = self.repository.vote_events("vote-plan", str(poll["id"]))
        with closing(open_room_database(self.repository.database_path)) as connection:
            plan = connection.execute(
                f"EXPLAIN QUERY PLAN {_VOTE_BALLOT_EVENTS_QUERY}",
                (
                    "vote-plan",
                    VISIBLE,
                    str(poll["id"]),
                    int(poll["seq"]),
                ),
            ).fetchall()

        plan_detail = "\n".join(str(row["detail"]) for row in plan)
        self.assertEqual(
            [event["id"] for event in vote_events],
            [poll["id"], ballot["id"]],
        )
        self.assertIn(f"USING INDEX {VOTE_BALLOT_INDEX_NAME}", plan_detail)
        self.assertNotIn("USE TEMP B-TREE", plan_detail)

    def test_deleted_room_rejects_a_late_transaction_without_restoring_canonical_rows(self) -> None:
        self.repository.create_room("deleted-room")
        self.repository.delete_room("deleted-room", reason="test cleanup")

        with self.assertRaisesRegex(ValueError, "deleted"):
            with self.repository.transaction("deleted-room") as transaction:
                transaction.upsert_participant(
                    {
                        "participant_id": "late-agent",
                        "display_name": "Late Agent",
                        "participant_type": "agent",
                    }
                )
                transaction.upsert_session(
                    {
                        "session_id": "late-agent",
                        "participant_id": "late-agent",
                        "status": "attached",
                    }
                )
                transaction.append_event(
                    "session_attached",
                    participant_id="late-agent",
                    session_id="late-agent",
                )

        self.assertEqual(self.repository.room("deleted-room"), {})
        self.assertEqual(self.repository.participant("deleted-room", "late-agent"), {})
        self.assertEqual(self.repository.session("deleted-room", "late-agent"), {})
        self.assertEqual(self.repository.read_events("deleted-room"), [])
        self.assertTrue(self.repository.room_is_deleted("deleted-room"))

    def test_participant_status_rolls_back_when_lifecycle_event_cannot_be_recorded(self) -> None:
        self.repository.create_room("participant-lifecycle")
        self.repository.upsert_participant(
            "participant-lifecycle",
            {
                "participant_id": "agent-a",
                "display_name": "Agent A",
                "participant_type": "agent",
            },
        )
        self.repository.upsert_session(
            "participant-lifecycle",
            {
                "session_id": "session-a",
                "participant_id": "agent-a",
                "status": "attached",
                "runtime_status": "idle",
            },
        )
        with closing(open_room_database(self.repository.database_path)) as connection:
            connection.execute(
                """CREATE TRIGGER reject_participant_kicked
                   BEFORE INSERT ON room_events
                   WHEN NEW.event_type = 'participant_kicked'
                   BEGIN
                       SELECT RAISE(ABORT, 'participant lifecycle event rejected');
                   END"""
            )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "participant lifecycle event rejected"):
            self.repository.set_participant_status(
                "participant-lifecycle",
                "agent-a",
                "kicked",
                reason="test rejection",
            )

        self.assertEqual(
            self.repository.participant("participant-lifecycle", "agent-a")["status"],
            "joined",
        )
        self.assertEqual(
            self.repository.session("participant-lifecycle", "session-a")["status"],
            "attached",
        )
        self.assertNotIn(
            "participant_kicked",
            [event["type"] for event in self.repository.read_events("participant-lifecycle")],
        )

    def test_room_status_rolls_back_when_lifecycle_event_cannot_be_recorded(self) -> None:
        self.repository.create_room("room-lifecycle", label="Lifecycle")
        with closing(open_room_database(self.repository.database_path)) as connection:
            connection.execute(
                """CREATE TRIGGER reject_room_archived
                   BEFORE INSERT ON room_events
                   WHEN NEW.event_type = 'room_archived'
                   BEGIN
                       SELECT RAISE(ABORT, 'room lifecycle event rejected');
                   END"""
            )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "room lifecycle event rejected"):
            self.repository.set_room_status("room-lifecycle", "archived")

        self.assertEqual(self.repository.room("room-lifecycle")["status"], "active")
        self.assertIn(
            "room-lifecycle",
            [room["room_id"] for room in self.repository.list_rooms()],
        )
        self.assertNotIn(
            "room_archived",
            [event["type"] for event in self.repository.read_events("room-lifecycle")],
        )


if __name__ == "__main__":
    unittest.main()
