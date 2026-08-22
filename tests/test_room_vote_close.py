from __future__ import annotations

import unittest

from agentsassemble.room.errors import RoomCommandRejected
from tests.test_room_realtime import HOST, RoomRealtimeControllerTests


class RoomVoteCloseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.room = RoomRealtimeControllerTests(
            methodName="test_controller_accepts_one_repository_instance_as_room_authority"
        )
        self.room.setUp()

    def tearDown(self) -> None:
        self.room.tearDown()

    def test_creator_can_close_early_and_other_participants_cannot(self) -> None:
        creator = {
            **HOST,
            "agent_id": "vote-creator",
            "display_name": "Creator",
            "operator": False,
        }
        other = {
            **HOST,
            "agent_id": "vote-other",
            "display_name": "Other",
            "operator": False,
        }
        self.room.controller.connect(creator)
        self.room.controller.connect(other)
        poll = self.room._command(
            "closable-vote",
            "message.send",
            {
                "kind": "vote",
                "vote_question": "지금 마칠까?",
                "vote_options": ["예", "아니오"],
                "vote_duration_seconds": 0,
            },
            creator,
        )["result"]["event"]

        with self.assertRaises(RoomCommandRejected) as rejected:
            self.room._command(
                "close-by-other",
                "message.send",
                {"kind": "vote_close", "vote_id": poll["id"]},
                other,
            )

        self.assertEqual(rejected.exception.code, "permission_denied")
        closed = self.room._command(
            "close-by-creator",
            "message.send",
            {"kind": "vote_close", "vote_id": poll["id"]},
            creator,
        )["result"]["event"]
        summary = self.room._command(
            "summary-after-close",
            "room.vote.summary",
            {"vote_id": poll["id"]},
            creator,
        )["result"]

        self.assertEqual(closed["message_kind"], "vote_close")
        self.assertTrue(summary["closed"])
        self.assertEqual(summary["closed_at"], closed["created_at"])
        with self.assertRaises(RoomCommandRejected) as cast_rejected:
            self.room._command(
                "cast-after-close",
                "message.send",
                {
                    "kind": "vote_cast",
                    "vote_id": poll["id"],
                    "vote_choice": "예",
                },
                creator,
            )
        self.assertEqual(cast_rejected.exception.code, "vote_closed")


if __name__ == "__main__":
    unittest.main()
