import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.legacy.meeting.operation_projection import review_checkpoint_request_operation_details
from agentsassemble.legacy.meeting.review_checkpoint import LegacyReviewCheckpointService
from agentsassemble.legacy.live_agent.runtime.operations import read_live_agent_operations


class LegacyReviewCheckpointServiceTests(unittest.TestCase):
    def service(self, output_root: Path) -> LegacyReviewCheckpointService:
        return LegacyReviewCheckpointService(
            output_root=output_root,
            process_supervisor=object(),
            turn_requester=lambda _root, _meeting_id, _payload: {},
        )

    def test_failed_create_records_bounded_prompt_free_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(ValueError, "Meeting missing was not found"):
                self.service(root).create(
                    "missing",
                    {
                        "group_id": "resident main",
                        "checkpoint_id": "checkpoint-1",
                        "agent_ids": ["agent-a"],
                        "content": "SECRET REVIEW PROMPT",
                        "timeout_seconds": 4,
                    },
                )

            operation = read_live_agent_operations(root, operation="review.checkpoint")[0]

        self.assertEqual(operation["status"], "failed")
        self.assertEqual(operation["target_id"], "missing")
        self.assertEqual(operation["details"]["group_id"], "resident-main")
        self.assertEqual(operation["details"]["agent_ids"], ["agent-a"])
        self.assertNotIn("SECRET REVIEW PROMPT", json.dumps(operation, ensure_ascii=False))

    def test_invalid_json_audit_and_projection_keep_only_safe_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.service(root).record_invalid_json()
            operation = read_live_agent_operations(root, operation="review.checkpoint")[0]

        details = review_checkpoint_request_operation_details(
            {"agent_ids": ["agent-a", 12, ""], "timeout_seconds": float("inf")},
            "room-a",
        )
        self.assertEqual(operation["status"], "failed")
        self.assertEqual(operation["target_id"], "")
        self.assertEqual(operation["details"], {})
        self.assertEqual(details["agent_ids"], ["agent-a"])
        self.assertEqual(details["timeout_seconds"], 30.0)


if __name__ == "__main__":
    unittest.main()
