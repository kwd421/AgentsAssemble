import tempfile
import unittest
from pathlib import Path

from agentsassemble.gui import _safe_static_path, append_lobby_event, build_meeting_payload, list_meetings, read_lobby
from agentsassemble.meeting_events import append_live_event, write_live_state
from agentsassemble.meeting import run_demo_meeting


class GuiServerTests(unittest.TestCase):
    def test_build_meeting_payload_contains_tabs_and_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_demo_meeting(adapter_name="mock", output_root=Path(temp_dir))

            payload = build_meeting_payload(result.meeting_dir)

            self.assertEqual(payload["meeting"]["meeting_id"], result.meeting_id)
            self.assertIn("agenda.md", payload["artifacts"])
            self.assertIn("transcript.md", payload["artifacts"])
            self.assertIn("decision.md", payload["artifacts"])
            self.assertIn("meeting.json", payload["artifacts"])
            self.assertIn("lore_lawyer/research.md", payload["research"])
            self.assertIn("lore_lawyer", payload["research_json"])
            self.assertIn("evidence_gate", payload["research_json"]["lore_lawyer"])
            self.assertIn("lore_lawyer.md", payload["return_packets"])
            self.assertEqual(payload["tabs"], ["lobby", "live", "board", "archive"])
            self.assertEqual(payload["tab_labels"]["lobby"], "로비")
            self.assertEqual(payload["tab_labels"]["live"], "실황")
            self.assertEqual(payload["tab_labels"]["board"], "작전판")
            self.assertEqual(payload["tab_labels"]["archive"], "아카이브")

    def test_lobby_events_are_appended_and_sanitized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            append_lobby_event(root, {"name": "seinel\nbad", "kind": "ready", "message": ""})
            append_lobby_event(root, {"name": "bad", "side": "???", "kind": "???", "message": "x"})
            append_lobby_event(
                root,
                {"name": "friend", "side": "other-agent", "kind": "message", "message": "만갤러 준비됐냐?"},
            )

            events = read_lobby(root)

            self.assertEqual(len(events), 3)
            self.assertEqual(events[0]["kind"], "ready")
            self.assertEqual(events[0]["side"], "other")
            self.assertEqual(events[0]["message"], "준비됐습니다.")
            self.assertEqual(events[0]["name"], "seinel bad")
            self.assertEqual(events[1]["kind"], "message")
            self.assertEqual(events[1]["side"], "other")
            self.assertEqual(events[2]["side"], "other-agent")
            self.assertEqual(events[2]["message"], "만갤러 준비됐냐?")

    def test_list_meetings_orders_latest_first(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = run_demo_meeting(adapter_name="mock", output_root=root)
            second = run_demo_meeting(adapter_name="mock", output_root=root)

            meetings = list_meetings(root)

            self.assertEqual(meetings[0]["meeting_id"], second.meeting_id)
            self.assertEqual(meetings[1]["meeting_id"], first.meeting_id)

    def test_live_meeting_payload_can_load_before_meeting_json_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "live-1"
            meeting_dir.mkdir(parents=True)
            write_live_state(
                meeting_dir,
                {
                    "meeting_id": "live-1",
                    "topic": "Live topic",
                    "question": "Live question?",
                    "roles": [],
                    "debate_rounds": [],
                    "moderator_synthesis": {},
                    "live_status": "running",
                },
            )
            append_live_event(meeting_dir, {"kind": "status", "content": "회의 시작"})

            meetings = list_meetings(root)
            payload = build_meeting_payload(meeting_dir)

            self.assertEqual(meetings[0]["meeting_id"], "live-1")
            self.assertEqual(payload["meeting"]["live_status"], "running")
            self.assertEqual(payload["live_events"][0]["content"], "회의 시작")

    def test_static_paths_cannot_escape_static_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            static_root = root / "static"
            static_root.mkdir()
            (static_root / "app.js").write_text("", encoding="utf-8")

            self.assertEqual(_safe_static_path(static_root, "app.js"), (static_root / "app.js").resolve())
            self.assertIsNone(_safe_static_path(static_root, "../secret.txt"))


if __name__ == "__main__":
    unittest.main()
