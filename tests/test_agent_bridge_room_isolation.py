from __future__ import annotations

import unittest

from agentsassemble.room.event_broker import RoomEventBroker


class AgentBridgeRoomIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.broker = RoomEventBroker(
            max_connections=16,
            max_public_connections=16,
            max_connections_per_session=8,
        )

    def tearDown(self) -> None:
        self.broker.close()

    @staticmethod
    def bridge_identity(room_id: str, participant_id: str, session_id: str) -> dict[str, object]:
        return {
            "client_type": "agent_bridge",
            "meeting_id": room_id,
            "agent_id": participant_id,
            "session_id": session_id,
        }

    def test_same_participant_in_different_rooms_has_independent_bridge_leases(self) -> None:
        room_a_first = self.broker.connect(
            self.bridge_identity("room-a", "agent-shared", "session-a-1")
        )
        room_b = self.broker.connect(
            self.bridge_identity("room-b", "agent-shared", "session-b-1")
        )

        self.assertEqual(self.broker.activate_bridge(room_a_first), 1)
        self.assertEqual(self.broker.activate_bridge(room_b), 1)

        room_a_replacement = self.broker.connect(
            self.bridge_identity("room-a", "agent-shared", "session-a-2")
        )
        self.assertEqual(self.broker.activate_bridge(room_a_replacement), 2)

        self.assertTrue(room_a_first.closed)
        self.assertFalse(room_a_replacement.closed)
        self.assertFalse(room_b.closed)
        self.assertTrue(self.broker.has_bridge("room-a", "agent-shared"))
        self.assertTrue(self.broker.has_bridge("room-b", "agent-shared"))

    def test_direct_bridge_delivery_never_crosses_room_boundary(self) -> None:
        room_a = self.broker.connect(
            self.bridge_identity("room-a", "agent-shared", "session-a")
        )
        room_b = self.broker.connect(
            self.bridge_identity("room-b", "agent-shared", "session-b")
        )
        self.broker.activate_bridge(room_a)
        self.broker.activate_bridge(room_b)

        assignment = {"op": "turn.assign", "turn_id": "turn-a"}
        self.assertTrue(
            self.broker.direct_to_bridge("room-a", "agent-shared", assignment)
        )
        self.assertEqual(room_a.drain(), [assignment])
        self.assertEqual(room_b.drain(), [])
        self.assertFalse(
            self.broker.direct_to_bridge("room-missing", "agent-shared", assignment)
        )

    def test_disconnect_participant_is_scoped_to_exact_room(self) -> None:
        room_a = self.broker.connect(
            self.bridge_identity("room-a", "agent-shared", "session-a")
        )
        room_b = self.broker.connect(
            self.bridge_identity("room-b", "agent-shared", "session-b")
        )
        self.broker.activate_bridge(room_a)
        self.broker.activate_bridge(room_b)

        self.broker.disconnect_participant("room-a", "agent-shared")

        self.assertTrue(room_a.closed)
        self.assertFalse(room_b.closed)
        self.assertFalse(self.broker.has_bridge("room-a", "agent-shared"))
        self.assertTrue(self.broker.has_bridge("room-b", "agent-shared"))


if __name__ == "__main__":
    unittest.main()
