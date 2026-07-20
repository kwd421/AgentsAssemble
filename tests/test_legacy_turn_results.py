import math
import unittest

from agentsassemble.legacy.meeting.core.turn_results import turn_sequence_result, turn_sequence_status


class LegacyTurnResultTests(unittest.TestCase):
    def test_result_projects_events_and_normalizes_timing(self) -> None:
        result = turn_sequence_result(
            2,
            {
                "status": "answered",
                "request_event": {"target_agent_id": "agent-a", "role_id": "critic"},
                "reply_event": {"id": "reply-1"},
                "elapsed_seconds": -1,
                "timeout_seconds": math.inf,
            },
        )

        self.assertEqual(result["index"], 2)
        self.assertEqual(result["agent_id"], "agent-a")
        self.assertEqual(result["role_id"], "critic")
        self.assertEqual(result["elapsed_seconds"], 0.0)
        self.assertEqual(result["timeout_seconds"], 0.0)

    def test_status_preserves_sequential_turn_precedence(self) -> None:
        self.assertEqual(turn_sequence_status(1, 0, 1, 0, turn_count=2), "stopped")
        self.assertEqual(turn_sequence_status(1, 1, 0, 0, turn_count=2), "timeout")
        self.assertEqual(turn_sequence_status(1, 0, 0, 1, turn_count=2), "cancelled")
        self.assertEqual(turn_sequence_status(2, 0, 0, 0, turn_count=2), "answered")
        self.assertEqual(turn_sequence_status(1, 0, 0, 0, turn_count=2), "degraded")


if __name__ == "__main__":
    unittest.main()
