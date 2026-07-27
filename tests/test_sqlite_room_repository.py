from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
