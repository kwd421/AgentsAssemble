import unittest

from agentsassemble.providers.bridge_protocol import (
    BridgeProtocolError,
    BridgeReportResponse,
    RoomWakeEnvelope,
    TurnAssignmentEnvelope,
)


def _assignment(**overrides):
    value = {
        "room_id": "general",
        "participant_id": "codex",
        "session_id": "codex-session",
        "turn_id": "turn-1",
        "provider_input": "hello",
        "timeout_seconds": 30,
    }
    value.update(overrides)
    return value


def _room_wake(**overrides):
    value = {
        "room_id": "general",
        "participant_id": "codex",
        "session_id": "codex-session",
        "turn_id": "wake-1",
        "input_up_to_seq": 7,
        "attachment_ids": [],
        "timeout_seconds": 30,
    }
    value.update(overrides)
    return value


class BridgeProtocolTests(unittest.TestCase):
    def parse_assignment(self, value):
        return TurnAssignmentEnvelope.parse_strict(
            value,
            room_id="general",
            participant_id="codex",
            session_id="codex-session",
        )

    def test_turn_assignment_requires_matching_identity_and_positive_timeout(self):
        parsed = self.parse_assignment(_assignment())

        self.assertEqual(parsed.turn_id, "turn-1")
        self.assertEqual(parsed.timeout_seconds, 30.0)
        for overrides in ({"room_id": "other"}, {"timeout_seconds": 0}, {"provider_input": ""}):
            with self.subTest(overrides=overrides), self.assertRaises(BridgeProtocolError):
                self.parse_assignment(_assignment(**overrides))

    def test_missing_turn_id_is_a_fatal_protocol_error(self):
        value = _assignment()
        value.pop("turn_id")

        with self.assertRaises(BridgeProtocolError) as raised:
            self.parse_assignment(value)

        self.assertTrue(raised.exception.fatal)
        self.assertEqual(raised.exception.code, "assignment_turn_id_missing")

    def test_room_wake_carries_the_assigned_room_sequence_without_transcript(self):
        parsed = RoomWakeEnvelope.parse_strict(
            _room_wake(),
            room_id="general",
            participant_id="codex",
            session_id="codex-session",
        )

        self.assertEqual(parsed.input_up_to_seq, 7)
        with self.assertRaises(BridgeProtocolError) as provider_input:
            RoomWakeEnvelope.parse_strict(
                _room_wake(provider_input="room transcript"),
                room_id="general",
                participant_id="codex",
                session_id="codex-session",
            )
        self.assertTrue(provider_input.exception.fatal)
        self.assertEqual(
            provider_input.exception.code,
            "room_wake_contains_provider_input",
        )

    def test_room_wake_rejects_a_missing_or_invalid_assigned_sequence(self):
        missing = _room_wake()
        missing.pop("input_up_to_seq")

        for value in (
            missing,
            _room_wake(input_up_to_seq=-1),
            _room_wake(input_up_to_seq=True),
            _room_wake(input_up_to_seq="7"),
        ):
            with self.subTest(value=value), self.assertRaises(BridgeProtocolError):
                RoomWakeEnvelope.parse_strict(
                    value,
                    room_id="general",
                    participant_id="codex",
                    session_id="codex-session",
                )

    def test_report_response_preserves_correlated_ack_and_nack(self):
        ack = BridgeReportResponse.parse({"op": "ack", "request_id": "req-1", "accepted": True})
        nack = BridgeReportResponse.parse(
            {
                "op": "nack",
                "request_id": "req-2",
                "accepted": False,
                "error": {"code": "stale_bridge_generation", "message": "superseded"},
            }
        )

        self.assertTrue(ack and ack.accepted)
        self.assertFalse(nack and nack.accepted)
        self.assertEqual(nack.code if nack else "", "stale_bridge_generation")


if __name__ == "__main__":
    unittest.main()
