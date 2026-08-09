from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.write_budget import RoomWriteBudget, RoomWriteBudgetPolicy
from agentsassemble.persistence.local.room.repository import RoomStore


class DurableRoomWriteBudgetTests(unittest.TestCase):
    def test_different_identities_cannot_shard_or_reset_the_room_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_store = RoomStore(root)
            first_store.create_room("general")
            wall_time = [1_000.0]
            policy = RoomWriteBudgetPolicy(
                window_seconds=60.0,
                max_commands_per_window=100,
                max_payload_bytes_per_window=100_000,
                max_room_commands_per_window=2,
                max_room_payload_bytes_per_window=100_000,
            )
            first_budget = RoomWriteBudget(
                first_store,
                policy=policy,
                wall_clock=lambda: wall_time[0],
            )
            first_budget.admit(
                room_id="general",
                principal_id="user-a:device-a",
                session_id="",
                request_id="first-write",
                action="message.send",
                payload={"content": "first"},
            )
            first_budget.admit(
                room_id="general",
                principal_id="user-b:device-b",
                session_id="",
                request_id="second-write",
                action="message.send",
                payload={"content": "second"},
            )

            restarted_budget = RoomWriteBudget(
                RoomStore(root),
                policy=policy,
                wall_clock=lambda: wall_time[0],
            )
            with self.assertRaises(RoomCommandRejected) as rejected:
                restarted_budget.admit(
                    room_id="general",
                    principal_id="user-c:device-c",
                    session_id="",
                    request_id="third-write",
                    action="message.send",
                    payload={"content": "must be rejected"},
                )

            self.assertEqual(rejected.exception.code, "write_budget_exceeded")
            wall_time[0] += 60.0
            restarted_budget.admit(
                room_id="general",
                principal_id="user-c:device-c",
                session_id="",
                request_id="next-window-write",
                action="message.send",
                payload={"content": "allowed in the next window"},
            )


if __name__ == "__main__":
    unittest.main()
