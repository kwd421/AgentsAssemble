import tempfile
import unittest
from pathlib import Path

from agentsassemble.live_agent_review_checkpoints import (
    review_checkpoint_file_stem,
    write_review_checkpoint_artifacts,
)


class LiveAgentReviewCheckpointArtifactTests(unittest.TestCase):
    def test_checkpoint_artifact_filename_is_path_safe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            result = write_review_checkpoint_artifacts(
                meeting_dir,
                {
                    "checkpoint_id": "../secret/checkpoint",
                    "meeting_id": "m1",
                    "group_id": "resident-main",
                    "status": "answered",
                    "turn_count": 1,
                    "answered_count": 1,
                    "results": [
                        {
                            "agent_id": "agent-a",
                            "status": "answered",
                            "request_event": {"id": "request-1", "content": "Review prompt."},
                            "reply_event": {"id": "reply-1", "content": "Review reply."},
                        }
                    ],
                },
            )

            self.assertEqual(review_checkpoint_file_stem("../secret/checkpoint"), "secret_checkpoint")
            self.assertEqual(result["artifact_path"], "review_checkpoints/secret_checkpoint.md")
            self.assertTrue((meeting_dir / result["artifact_path"]).exists())
            self.assertFalse((meeting_dir.parent / "secret" / "checkpoint.md").exists())


if __name__ == "__main__":
    unittest.main()
