import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.memory import (
    load_memory_context,
    write_memory_artifacts,
)
from agentsassemble.models import Role


def _make_meeting(meeting_id="test-001", **overrides):
    base = {
        "meeting_id": meeting_id,
        "topic": "topic",
        "display_topic": "표시 주제",
        "question": "question",
        "display_question": "표시 질문",
        "moderator_synthesis": {
            "winner": "Option A",
            "confidence": "high",
            "summary": "A wins.",
            "caveats": ["caveat1"],
            "tasks": {"role_a": "Do X"},
        },
        "evidence_gate": {"status": "pass", "total_supported_claims": 3, "total_unsupported_claims": 1},
        "research_depth": {"name": "standard"},
        "research_steering": {"prompt": "focus on X"},
        "roles": [{"id": "role_a", "display_name": "Role A", "lens": "lens_a"}],
        "debate_rounds": [
            {
                "title": "Round 1",
                "messages": [{"role_id": "role_a", "round": "round_1", "content": "I argue A."}],
            }
        ],
        "memory_input": {
            "research_summaries": [{"role_id": "role_a", "summary": "Found evidence for A."}]
        },
        "audit_metadata": {"created_at": "2026-01-01T00:00:00+00:00"},
        "artifacts": {"decision": "meetings/test-001/decision.md"},
    }
    base.update(overrides)
    return base


class TestWriteMemoryArtifacts(unittest.TestCase):
    def test_creates_expected_directory_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meeting = _make_meeting()
            result = write_memory_artifacts(root, meeting)

            self.assertTrue((root / "memory" / "project.md").exists())
            self.assertTrue((root / "memory" / "episodes.jsonl").exists())
            self.assertTrue((root / "memory" / "agents" / "role_a.md").exists())
            self.assertTrue((root / "memory" / "reflections" / "test-001.md").exists())
            self.assertEqual(result["project"], "memory/project.md")
            self.assertEqual(result["agents"], "memory/agents/")
            self.assertEqual(result["episodes"], "memory/episodes.jsonl")
            self.assertEqual(result["reflection"], "memory/reflections/test-001.md")

    def test_project_memory_contains_meeting_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_artifacts(root, _make_meeting())
            content = (root / "memory" / "project.md").read_text(encoding="utf-8")

            self.assertIn("## Meeting test-001", content)
            self.assertIn("Decision: Option A", content)
            self.assertIn("Confidence: high", content)
            self.assertIn("A wins.", content)

    def test_agent_memory_contains_role_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_artifacts(root, _make_meeting())
            content = (root / "memory" / "agents" / "role_a.md").read_text(encoding="utf-8")

            self.assertIn("Role: Role A", content)
            self.assertIn("Lens: lens_a", content)
            self.assertIn("Task: Do X", content)
            self.assertIn("Found evidence for A.", content)
            self.assertIn("I argue A.", content)

    def test_episode_jsonl_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_artifacts(root, _make_meeting())
            lines = (root / "memory" / "episodes.jsonl").read_text(encoding="utf-8").strip().splitlines()

            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["meeting_id"], "test-001")
            self.assertEqual(record["decision"], "Option A")
            self.assertEqual(record["confidence"], "high")
            self.assertEqual(record["topic"], "topic")
            self.assertEqual(record["display_topic"], "표시 주제")

    def test_multiple_writes_append_episodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_artifacts(root, _make_meeting("m1"))
            write_memory_artifacts(root, _make_meeting("m2"))
            lines = (root / "memory" / "episodes.jsonl").read_text(encoding="utf-8").strip().splitlines()

            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["meeting_id"], "m1")
            self.assertEqual(json.loads(lines[1])["meeting_id"], "m2")

    def test_multiple_writes_append_project_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_artifacts(root, _make_meeting("m1"))
            write_memory_artifacts(root, _make_meeting("m2"))
            content = (root / "memory" / "project.md").read_text(encoding="utf-8")

            self.assertIn("## Meeting m1", content)
            self.assertIn("## Meeting m2", content)

    def test_reflection_document_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_artifacts(root, _make_meeting())
            content = (root / "memory" / "reflections" / "test-001.md").read_text(encoding="utf-8")

            self.assertIn("# Reflection: test-001", content)
            self.assertIn("Winner: Option A", content)
            self.assertIn("caveat1", content)
            self.assertIn("role_a: Do X", content)


class TestLoadMemoryContext(unittest.TestCase):
    def test_empty_root_returns_empty_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            roles = [Role(id="role_a", display_name="A", lens="l", research_focus="f")]
            ctx = load_memory_context(root, roles)

            self.assertEqual(ctx["project_memory"], "")
            self.assertEqual(ctx["agent_memories"], {"role_a": ""})
            self.assertEqual(ctx["recent_episodes"], [])

    def test_round_trip_write_then_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_artifacts(root, _make_meeting())
            roles = [Role(id="role_a", display_name="A", lens="l", research_focus="f")]
            ctx = load_memory_context(root, roles)

            self.assertIn("## Meeting test-001", ctx["project_memory"])
            self.assertIn("Role: Role A", ctx["agent_memories"]["role_a"])
            self.assertEqual(len(ctx["recent_episodes"]), 1)
            self.assertEqual(ctx["recent_episodes"][0]["meeting_id"], "test-001")

    def test_recent_episodes_limited_to_5(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(8):
                write_memory_artifacts(root, _make_meeting(f"m{i}"))
            roles = [Role(id="role_a", display_name="A", lens="l", research_focus="f")]
            ctx = load_memory_context(root, roles)

            self.assertEqual(len(ctx["recent_episodes"]), 5)
            self.assertEqual(ctx["recent_episodes"][0]["meeting_id"], "m3")
            self.assertEqual(ctx["recent_episodes"][-1]["meeting_id"], "m7")

    def test_malformed_jsonl_lines_tolerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mem = root / "memory"
            mem.mkdir(parents=True)
            ep = mem / "episodes.jsonl"
            ep.write_text(
                'not json\n{"meeting_id":"ok"}\n\n{"bad":tru}\n{"meeting_id":"ok2"}\n',
                encoding="utf-8",
            )
            roles = [Role(id="x", display_name="X", lens="l", research_focus="f")]
            ctx = load_memory_context(root, roles)

            self.assertEqual(len(ctx["recent_episodes"]), 2)
            self.assertEqual(ctx["recent_episodes"][0]["meeting_id"], "ok")
            self.assertEqual(ctx["recent_episodes"][1]["meeting_id"], "ok2")

    def test_missing_role_agent_memory_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_artifacts(root, _make_meeting())
            roles = [
                Role(id="role_a", display_name="A", lens="l", research_focus="f"),
                Role(id="nonexistent", display_name="N", lens="l", research_focus="f"),
            ]
            ctx = load_memory_context(root, roles)

            self.assertNotEqual(ctx["agent_memories"]["role_a"], "")
            self.assertEqual(ctx["agent_memories"]["nonexistent"], "")


class TestEdgeCases(unittest.TestCase):
    def test_meeting_with_no_synthesis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meeting = {"meeting_id": "bare", "roles": [{"id": "r", "display_name": "R", "lens": "L"}]}
            write_memory_artifacts(root, meeting)

            content = (root / "memory" / "project.md").read_text(encoding="utf-8")
            self.assertIn("Decision: Undetermined", content)
            self.assertIn("Confidence: low", content)

    def test_meeting_with_no_debate_rounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meeting = _make_meeting(debate_rounds=[])
            write_memory_artifacts(root, meeting)
            content = (root / "memory" / "agents" / "role_a.md").read_text(encoding="utf-8")
            self.assertIn("### Public Contributions", content)

    def test_meeting_with_no_research_summaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meeting = _make_meeting(memory_input={})
            write_memory_artifacts(root, meeting)
            content = (root / "memory" / "agents" / "role_a.md").read_text(encoding="utf-8")
            self.assertIn("No research summary recorded.", content)

    def test_episode_uses_audit_created_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_memory_artifacts(root, _make_meeting())
            ep = (root / "memory" / "episodes.jsonl").read_text(encoding="utf-8")
            record = json.loads(ep.strip())
            self.assertEqual(record["created_at"], "2026-01-01T00:00:00+00:00")

    def test_episode_falls_back_to_now_when_no_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meeting = _make_meeting(audit_metadata={})
            write_memory_artifacts(root, meeting)
            ep = (root / "memory" / "episodes.jsonl").read_text(encoding="utf-8")
            record = json.loads(ep.strip())
            # Should be a valid ISO timestamp (not empty)
            self.assertIn("T", record["created_at"])


if __name__ == "__main__":
    unittest.main()
