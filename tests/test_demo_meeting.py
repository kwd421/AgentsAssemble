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
            self.assertEqual(meeting["research_depth"]["name"], "smoke")
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

    def test_research_depth_changes_mock_source_volume(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            smoke = run_demo_meeting(adapter_name="mock", output_root=root, research_depth="smoke")
            deep = run_demo_meeting(adapter_name="mock", output_root=root, research_depth="deep")

            smoke_research = json.loads(
                (smoke.meeting_dir / "private_research" / "lore_lawyer" / "research.json").read_text(
                    encoding="utf-8"
                )
            )
            deep_research = json.loads(
                (deep.meeting_dir / "private_research" / "lore_lawyer" / "research.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(smoke_research["research_depth"]["name"], "smoke")
            self.assertEqual(deep_research["research_depth"]["name"], "deep")
            self.assertLess(len(smoke_research["sources"]), len(deep_research["sources"]))
            self.assertEqual(len(smoke_research["sources"]), 8)
            self.assertEqual(len(deep_research["sources"]), 45)
            self.assertEqual(len(smoke_research["claim_evidence"]), 3)
            self.assertEqual(len(deep_research["claim_evidence"]), 12)
            self.assertEqual(len(smoke_research["counterclaims"]), 1)
            self.assertEqual(len(deep_research["counterclaims"]), 6)

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
