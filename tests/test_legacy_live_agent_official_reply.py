import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.legacy_live_agent_official_reply import LegacyLiveAgentOfficialReplyService
from agentsassemble.live_agent_operations import read_live_agent_operations


class LegacyLiveAgentOfficialReplyServiceTests(unittest.TestCase):
    def test_official_reply_records_bounded_identity_without_content(self) -> None:
        result = {
            "event": {
                "meeting_id": "meeting-a",
                "source_event_id": "source-a",
                "role_id": "role-a",
                "turn_id": "turn-a",
                "turn_index": 2,
                "content": "private official reply",
            },
            "shared_memory": {
                "shared_memory_official_event_count": 3,
                "shared_memory_last_event_id": "event-a",
                "private_notes": "do not persist",
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch(
                "agentsassemble.legacy_live_agent_official_reply.live_agent_official_turn_payload",
                return_value=result,
            ):
                returned = LegacyLiveAgentOfficialReplyService(root).reply(
                    "agent-a",
                    {"meeting_id": "meeting-a", "content": "private official reply"},
                )
            operation = read_live_agent_operations(root, operation="official_turn.reply")[0]

        self.assertIs(returned, result)
        self.assertEqual(operation["status"], "success")
        self.assertEqual(operation["target_id"], "agent-a")
        self.assertEqual(operation["details"]["turn_index"], 2)
        self.assertEqual(operation["details"]["shared_memory_official_event_count"], 3)
        self.assertNotIn("private official reply", str(operation))
        self.assertNotIn("do not persist", str(operation))

    def test_review_reply_uses_review_operation_and_checkpoint(self) -> None:
        result = {
            "event": {
                "meeting_id": "meeting-a",
                "source_event_id": "source-a",
                "review_checkpoint_id": "review-a",
            },
            "shared_memory": {},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch(
                "agentsassemble.legacy_live_agent_official_reply.live_agent_official_turn_payload",
                return_value=result,
            ):
                LegacyLiveAgentOfficialReplyService(root).reply("agent-a", {})
            operation = read_live_agent_operations(root, operation="review.reply")[0]

        self.assertEqual(operation["status"], "success")
        self.assertEqual(operation["details"]["review_checkpoint_id"], "review-a")

    def test_domain_failure_records_request_identity_and_reraises(self) -> None:
        payload = {
            "meeting_id": "meeting-a",
            "source_event_id": "source-a",
            "role_id": "role-a",
            "turn_id": "turn-a",
            "turn_index": 1,
            "content": "private official reply",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch(
                "agentsassemble.legacy_live_agent_official_reply.live_agent_official_turn_payload",
                side_effect=ValueError("Matching official turn request was not found."),
            ):
                with self.assertRaisesRegex(ValueError, "Matching official turn request"):
                    LegacyLiveAgentOfficialReplyService(root).reply("agent-a", payload)
            operation = read_live_agent_operations(root, operation="official_turn.reply")[0]

        self.assertEqual(operation["status"], "failed")
        self.assertEqual(operation["details"]["source_event_id"], "source-a")
        self.assertEqual(operation["details"]["turn_index"], 1)
        self.assertNotIn("private official reply", str(operation))


if __name__ == "__main__":
    unittest.main()
