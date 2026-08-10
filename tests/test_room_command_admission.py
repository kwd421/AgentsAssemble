import tempfile
import unittest
from pathlib import Path

from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.command_admission import authorize_room_command
from agentsassemble.room.realtime import RoomRealtimeController
from agentsassemble.room.write_budget import RoomWriteBudgetPolicy
from tests.room_realtime_test_support import (
    FakeBridgeManager,
    memory_room_access_services,
)


HOST = {
    "agent_id": "operator-local",
    "display_name": "Host",
    "participant_type": "human",
    "client_type": "browser",
    "invite_scope": "read_write",
    "meeting_id": "general",
    "operator": True,
}


class RoomCommandAdmissionTests(unittest.TestCase):
    def test_read_only_bridge_cannot_publish_or_open_provider_requests(self):
        read_only_bridge = {
            "client_type": "agent_bridge",
            "invite_scope": "read_only",
        }
        read_write_bridge = {
            "client_type": "agent_bridge",
            "invite_scope": "room",
        }

        for action in (
            "message.final",
            "room.result.publish",
            "provider.request.open",
            "provider.request.closed",
        ):
            with self.subTest(action=action):
                with self.assertRaises(RoomCommandRejected) as rejected:
                    authorize_room_command(read_only_bridge, action)
                self.assertEqual(rejected.exception.code, "permission_denied")
                authorize_room_command(read_write_bridge, action)

    def test_denied_write_does_not_consume_another_principals_room_budget(self):
        policy = RoomWriteBudgetPolicy(
            max_commands_per_window=10,
            max_payload_bytes_per_window=100_000,
            max_room_commands_per_window=1,
            max_room_payload_bytes_per_window=100_000,
        )
        access = memory_room_access_services()
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = RoomRealtimeController(
                Path(temp_dir),
                **access.controller_kwargs(),
                providers=[],
                bridge_manager=FakeBridgeManager(),
                write_budget_policy=policy,
            )
            controller.store.upsert_participant(
                "general",
                {
                    "participant_id": HOST["agent_id"],
                    "display_name": HOST["display_name"],
                    "participant_type": "human",
                    "status": "joined",
                },
            )
            read_only = {
                **HOST,
                "agent_id": "read-only-guest",
                "operator": False,
                "invite_scope": "read_only",
            }

            with self.assertRaises(RoomCommandRejected) as denied:
                controller.handle_command(
                    read_only,
                    {
                        "op": "command",
                        "request_id": "denied-write",
                        "action": "message.send",
                        "payload": {"content": "must not consume shared quota"},
                    },
                )

            accepted = controller.handle_command(
                HOST,
                {
                    "op": "command",
                    "request_id": "authorized-write",
                    "action": "message.send",
                    "payload": {"content": "authorized message"},
                },
            )
            controller.close()

        self.assertEqual(denied.exception.code, "permission_denied")
        self.assertTrue(accepted["accepted"])


if __name__ == "__main__":
    unittest.main()
