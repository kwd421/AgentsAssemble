import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.meeting import run_demo_meeting


class DemoMeetingTests(unittest.TestCase):
    def test_mock_demo_creates_required_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_demo_meeting(adapter_name="mock", output_root=Path(temp_dir))

            meeting_dir = result.meeting_dir
            self.assertTrue(meeting_dir.exists())

            self.assertTrue((meeting_dir / "agenda.md").exists())
            self.assertTrue((meeting_dir / "transcript.md").exists())
            self.assertTrue((meeting_dir / "decision.md").exists())
            self.assertTrue((meeting_dir / "meeting.json").exists())

            meeting = json.loads((meeting_dir / "meeting.json").read_text(encoding="utf-8"))
            self.assertEqual(meeting["adapter_config"]["name"], "mock")
            self.assertEqual(meeting["question"], "Who is the strongest One Piece admiral?")
            self.assertEqual(
                [role["id"] for role in meeting["roles"]],
                ["lore_lawyer", "show_me_the_feats", "fanboard_skeptic"],
            )
            self.assertEqual(
                [role["display_name"] for role in meeting["roles"]],
                ["설정충", "공식이뭘알아", "만갤러"],
            )

            transcript = (meeting_dir / "transcript.md").read_text(encoding="utf-8")
            self.assertIn("## Round 1", transcript)
            self.assertIn("## Round 2", transcript)
            self.assertIn("## Moderator Synthesis", transcript)

            for role_id in ("lore_lawyer", "show_me_the_feats", "fanboard_skeptic"):
                self.assertTrue((meeting_dir / "private_research" / role_id / "research.md").exists())
                self.assertTrue((meeting_dir / "private_research" / role_id / "research.json").exists())
                self.assertTrue((meeting_dir / "roles" / role_id / "memory.md").exists())
                self.assertTrue((meeting_dir / "roles" / role_id / "history.jsonl").exists())
                self.assertTrue((meeting_dir / "tasks" / f"{role_id}.md").exists())

    def test_round_one_does_not_include_other_private_research(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_demo_meeting(adapter_name="mock", output_root=Path(temp_dir))
            meeting = json.loads((result.meeting_dir / "meeting.json").read_text(encoding="utf-8"))

            round_one = meeting["debate_rounds"][0]["messages"]
            for message in round_one:
                own_role = message["role_id"]
                for other_role in ("lore_lawyer", "show_me_the_feats", "fanboard_skeptic"):
                    if other_role != own_role:
                        self.assertNotIn(f"private_research/{other_role}", message["content"])


if __name__ == "__main__":
    unittest.main()
