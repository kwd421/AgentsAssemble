import unittest

from agentsassemble.room.commands import (
    RoomCommandValidationError,
    capabilities_for_identity,
    parse_room_command,
)


class RoomCommandPolicyTests(unittest.TestCase):
    def test_parse_returns_normalized_command(self):
        command = parse_room_command(
            {
                "request_id": " req-1 ",
                "action": "message.send",
                "payload": {"content": "hello"},
            }
        )

        self.assertEqual(command.request_id, "req-1")
        self.assertEqual(command.action, "message.send")
        self.assertEqual(command.payload, {"content": "hello"})

    def test_parse_rejects_missing_request_and_unknown_action(self):
        with self.assertRaises(RoomCommandValidationError) as missing:
            parse_room_command({"action": "message.send"})
        with self.assertRaises(RoomCommandValidationError) as unknown:
            parse_room_command({"request_id": "req", "action": "unknown"})

        self.assertEqual(missing.exception.code, "bad_request")
        self.assertEqual(unknown.exception.code, "unknown_action")

    def test_capabilities_distinguish_operator_guest_and_bridge(self):
        operator = capabilities_for_identity({"operator": True, "client_type": "browser"})
        read_only = capabilities_for_identity(
            {"operator": False, "client_type": "browser", "invite_scope": "read_only"}
        )
        bridge = capabilities_for_identity({"operator": False, "client_type": "agent_bridge"})

        self.assertTrue(operator["agent.control"])
        self.assertTrue(operator["room.manage"])
        self.assertTrue(operator["message.send"])
        self.assertFalse(read_only["message.send"])
        self.assertTrue(read_only["room.history"])
        self.assertTrue(read_only["room.vote.summary"])
        self.assertTrue(bridge["bridge.report"])
        self.assertFalse(bridge["room.manage"])
        self.assertFalse(bridge["room.history"])
        self.assertFalse(bridge["room.vote.summary"])


if __name__ == "__main__":
    unittest.main()
