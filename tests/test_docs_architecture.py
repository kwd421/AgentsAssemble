import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocsArchitectureTests(unittest.TestCase):
    def test_live_session_room_model_documents_shared_room_semantics(self):
        doc = (ROOT / "docs" / "live-session-room-model.md").read_text(encoding="utf-8")

        self.assertIn("shared room event stream", doc)
        self.assertIn("Agents do not receive isolated interview prompts", doc)
        self.assertIn("human", doc)
        self.assertIn("local_cli delegate", doc)
        self.assertIn("live_session", doc)
        self.assertIn("Codex app", doc)
        self.assertIn("CLI", doc)
        self.assertIn("memory capsule", doc)
        self.assertIn("advisory policy envelope", doc)

    def test_provider_architecture_points_to_live_session_model(self):
        doc = (ROOT / "docs" / "provider-architecture.md").read_text(encoding="utf-8")

        self.assertIn("Shared Room Event Stream", doc)
        self.assertIn("docs/live-session-room-model.md", doc)
        self.assertIn("live_session", doc)
        self.assertIn("advisory", doc)

    def test_roadmap_mentions_room_event_log_and_live_session_adapter(self):
        doc = (ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")

        self.assertIn("Room Event Log", doc)
        self.assertIn("live_session adapter", doc)
        self.assertIn("Decision Gate", doc)

    def test_live_room_references_preserve_council_boundary(self):
        live_model = (ROOT / "docs" / "live-session-room-model.md").read_text(encoding="utf-8")
        provider_arch = (ROOT / "docs" / "provider-architecture.md").read_text(encoding="utf-8")
        roadmap = (ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")
        research_log = (ROOT / "docs" / "research-log.md").read_text(encoding="utf-8")
        combined = "\n".join([live_model, provider_arch, roadmap, research_log])

        self.assertIn("Stoops", combined)
        self.assertIn("Claude Code Channels", combined)
        self.assertIn("live room infrastructure", combined)
        self.assertIn("council workflow", combined)
        self.assertIn("free chat", combined)
        self.assertIn("official", combined)


if __name__ == "__main__":
    unittest.main()
