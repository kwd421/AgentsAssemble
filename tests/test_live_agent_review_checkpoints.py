import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.legacy.live_agent.runtime.review_checkpoints import (
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

    def test_checkpoint_artifact_filename_avoids_distinct_id_collisions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            first = write_review_checkpoint_artifacts(
                meeting_dir,
                {
                    "checkpoint_id": "a/b",
                    "meeting_id": "m1",
                    "group_id": "resident-main",
                    "status": "answered",
                    "turn_count": 1,
                    "answered_count": 1,
                    "results": [
                        {
                            "agent_id": "agent-a",
                            "status": "answered",
                            "request_event": {"id": "request-1", "content": "First prompt."},
                            "reply_event": {"id": "reply-1", "content": "First reply."},
                        }
                    ],
                },
            )
            second = write_review_checkpoint_artifacts(
                meeting_dir,
                {
                    "checkpoint_id": "a:b",
                    "meeting_id": "m1",
                    "group_id": "resident-main",
                    "status": "answered",
                    "turn_count": 1,
                    "answered_count": 1,
                    "results": [
                        {
                            "agent_id": "agent-b",
                            "status": "answered",
                            "request_event": {"id": "request-2", "content": "Second prompt."},
                            "reply_event": {"id": "reply-2", "content": "Second reply."},
                        }
                    ],
                },
            )

            self.assertEqual(review_checkpoint_file_stem("a/b"), "a_b")
            self.assertEqual(review_checkpoint_file_stem("a:b"), "a_b")
            self.assertEqual(first["artifact_path"], "review_checkpoints/a_b.md")
            self.assertEqual(second["artifact_path"], "review_checkpoints/a_b-2.md")
            self.assertIn("First reply.", (meeting_dir / first["artifact_path"]).read_text(encoding="utf-8"))
            self.assertIn("Second reply.", (meeting_dir / second["artifact_path"]).read_text(encoding="utf-8"))
            first_json = json.loads((meeting_dir / first["artifact_json_path"]).read_text(encoding="utf-8"))
            second_json = json.loads((meeting_dir / second["artifact_json_path"]).read_text(encoding="utf-8"))
            self.assertEqual(first_json["checkpoint_id"], "a/b")
            self.assertEqual(second_json["checkpoint_id"], "a:b")

    def test_checkpoint_artifact_filename_uses_raw_identity_for_cleaned_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            first_id = "same\nstem"
            second_id = "same\rstem"

            first = write_review_checkpoint_artifacts(
                meeting_dir,
                {
                    "checkpoint_id": first_id,
                    "status": "answered",
                    "results": [{"request_event": {"content": "First prompt."}, "reply_event": {"content": "First reply."}}],
                },
            )
            second = write_review_checkpoint_artifacts(
                meeting_dir,
                {
                    "checkpoint_id": second_id,
                    "status": "answered",
                    "results": [{"request_event": {"content": "Second prompt."}, "reply_event": {"content": "Second reply."}}],
                },
            )

            self.assertEqual(review_checkpoint_file_stem(first_id), "same_stem")
            self.assertEqual(review_checkpoint_file_stem(second_id), "same_stem")
            self.assertEqual(first["artifact_path"], "review_checkpoints/same_stem.md")
            self.assertEqual(second["artifact_path"], "review_checkpoints/same_stem-2.md")
            first_json = json.loads((meeting_dir / first["artifact_json_path"]).read_text(encoding="utf-8"))
            second_json = json.loads((meeting_dir / second["artifact_json_path"]).read_text(encoding="utf-8"))
            self.assertEqual(first_json["checkpoint_id"], first_id)
            self.assertEqual(second_json["checkpoint_id"], second_id)

    def test_checkpoint_artifact_filename_uses_raw_identity_for_whitespace_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            first_id = "checkpoint"
            second_id = " checkpoint "
            third_id = "checkpoint\n"

            first = write_review_checkpoint_artifacts(
                meeting_dir,
                {
                    "checkpoint_id": first_id,
                    "status": "answered",
                    "results": [{"request_event": {"content": "First prompt."}, "reply_event": {"content": "First reply."}}],
                },
            )
            second = write_review_checkpoint_artifacts(
                meeting_dir,
                {
                    "checkpoint_id": second_id,
                    "status": "answered",
                    "results": [{"request_event": {"content": "Second prompt."}, "reply_event": {"content": "Second reply."}}],
                },
            )
            third = write_review_checkpoint_artifacts(
                meeting_dir,
                {
                    "checkpoint_id": third_id,
                    "status": "answered",
                    "results": [{"request_event": {"content": "Third prompt."}, "reply_event": {"content": "Third reply."}}],
                },
            )

            self.assertEqual(review_checkpoint_file_stem(first_id), "checkpoint")
            self.assertEqual(review_checkpoint_file_stem(second_id), "checkpoint")
            self.assertEqual(review_checkpoint_file_stem(third_id), "checkpoint")
            self.assertEqual(first["artifact_path"], "review_checkpoints/checkpoint.md")
            self.assertEqual(second["artifact_path"], "review_checkpoints/checkpoint-2.md")
            self.assertEqual(third["artifact_path"], "review_checkpoints/checkpoint-3.md")
            first_json = json.loads((meeting_dir / first["artifact_json_path"]).read_text(encoding="utf-8"))
            second_json = json.loads((meeting_dir / second["artifact_json_path"]).read_text(encoding="utf-8"))
            third_json = json.loads((meeting_dir / third["artifact_json_path"]).read_text(encoding="utf-8"))
            self.assertEqual(first_json["checkpoint_id"], first_id)
            self.assertEqual(second_json["checkpoint_id"], second_id)
            self.assertEqual(third_json["checkpoint_id"], third_id)

    def test_checkpoint_artifact_filename_reuses_exact_same_raw_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            checkpoint_id = " checkpoint "

            first = write_review_checkpoint_artifacts(
                meeting_dir,
                {
                    "checkpoint_id": checkpoint_id,
                    "status": "answered",
                    "results": [{"request_event": {"content": "First prompt."}, "reply_event": {"content": "First reply."}}],
                },
            )
            second = write_review_checkpoint_artifacts(
                meeting_dir,
                {
                    "checkpoint_id": checkpoint_id,
                    "status": "answered",
                    "results": [{"request_event": {"content": "Second prompt."}, "reply_event": {"content": "Second reply."}}],
                },
            )

            self.assertEqual(first["artifact_path"], "review_checkpoints/checkpoint.md")
            self.assertEqual(second["artifact_path"], first["artifact_path"])
            artifact_text = (meeting_dir / second["artifact_path"]).read_text(encoding="utf-8")
            artifact_json = json.loads((meeting_dir / second["artifact_json_path"]).read_text(encoding="utf-8"))
            self.assertNotIn("First reply.", artifact_text)
            self.assertIn("Second reply.", artifact_text)
            self.assertEqual(artifact_json["checkpoint_id"], checkpoint_id)

    def test_checkpoint_artifact_filename_preserves_falsy_non_blank_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            first = write_review_checkpoint_artifacts(
                meeting_dir,
                {
                    "checkpoint_id": 0,
                    "status": "answered",
                    "results": [{"request_event": {"content": "First prompt."}, "reply_event": {"content": "First reply."}}],
                },
            )
            second = write_review_checkpoint_artifacts(
                meeting_dir,
                {
                    "checkpoint_id": False,
                    "status": "answered",
                    "results": [{"request_event": {"content": "Second prompt."}, "reply_event": {"content": "Second reply."}}],
                },
            )

            self.assertEqual(first["artifact_path"], "review_checkpoints/0.md")
            self.assertEqual(second["artifact_path"], "review_checkpoints/False.md")
            first_json = json.loads((meeting_dir / first["artifact_json_path"]).read_text(encoding="utf-8"))
            second_json = json.loads((meeting_dir / second["artifact_json_path"]).read_text(encoding="utf-8"))
            self.assertEqual(first_json["checkpoint_id"], "0")
            self.assertEqual(second_json["checkpoint_id"], "False")

    def test_checkpoint_artifact_filename_uses_raw_identity_for_truncated_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            first_id = f"{'x' * 140}A"
            second_id = f"{'x' * 140}B"
            expected_stem = "x" * 96

            first = write_review_checkpoint_artifacts(
                meeting_dir,
                {
                    "checkpoint_id": first_id,
                    "status": "answered",
                    "results": [{"request_event": {"content": "First prompt."}, "reply_event": {"content": "First reply."}}],
                },
            )
            second = write_review_checkpoint_artifacts(
                meeting_dir,
                {
                    "checkpoint_id": second_id,
                    "status": "answered",
                    "results": [{"request_event": {"content": "Second prompt."}, "reply_event": {"content": "Second reply."}}],
                },
            )

            self.assertEqual(review_checkpoint_file_stem(first_id), expected_stem)
            self.assertEqual(review_checkpoint_file_stem(second_id), expected_stem)
            self.assertEqual(first["artifact_path"], f"review_checkpoints/{expected_stem}.md")
            self.assertEqual(second["artifact_path"], f"review_checkpoints/{expected_stem}-2.md")
            first_json = json.loads((meeting_dir / first["artifact_json_path"]).read_text(encoding="utf-8"))
            second_json = json.loads((meeting_dir / second["artifact_json_path"]).read_text(encoding="utf-8"))
            self.assertEqual(first_json["checkpoint_id"], first_id)
            self.assertEqual(second_json["checkpoint_id"], second_id)


if __name__ == "__main__":
    unittest.main()
