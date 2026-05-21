import json
import tempfile
import time
import unittest
from pathlib import Path

from agentsassemble.live_meeting_memory import (
    build_live_meeting_memory,
    projected_live_meeting_memory_artifacts,
    render_action_items,
    render_open_questions,
    render_rolling_summary,
    write_live_meeting_memory_artifacts,
)
from agentsassemble.meeting_events import append_live_event, read_live_events


class LiveMeetingMemoryTests(unittest.TestCase):
    def test_build_live_meeting_memory_uses_only_official_record_events(self):
        events = [
            {
                "id": "request-1",
                "kind": "live_agent_turn_request",
                "channel": "system",
                "official_record": False,
                "content": "private moderator prompt",
            },
            {
                "id": "reply-1",
                "created_at": "2026-05-22T01:02:03+00:00",
                "kind": "message",
                "channel": "official",
                "official_record": True,
                "actor_id": "agent-a",
                "role_id": "architect",
                "display_name": "Architect",
                "source_event_id": "request-1",
                "content": "\n".join(
                    [
                        "We should keep resident sessions explicit.",
                        "Decision: Require host approval before starting provider CLIs.",
                        "Action: Add shared memory artifacts.",
                        "Question: Should play chatter be promoted?",
                    ]
                ),
            },
            {
                "id": "review-1",
                "kind": "message",
                "channel": "review",
                "official_record": False,
                "display_name": "Reviewer",
                "content": "Action: leak review-only note",
            },
            {
                "id": "status-1",
                "kind": "status",
                "official_record": False,
                "content": "private status",
            },
        ]

        memory = build_live_meeting_memory(events, meeting={"meeting_id": "resident-m1", "topic": "Resident Memory"})

        self.assertEqual(memory["meeting_id"], "resident-m1")
        self.assertEqual(memory["official_event_count"], 1)
        self.assertEqual(memory["last_official_event_id"], "reply-1")
        self.assertIn("Resident Memory", memory["topic"])
        self.assertEqual(
            memory["decisions"],
            [{"event_id": "reply-1", "speaker": "Architect", "text": "Require host approval before starting provider CLIs."}],
        )
        self.assertEqual(
            memory["action_items"],
            [{"event_id": "reply-1", "speaker": "Architect", "text": "Add shared memory artifacts."}],
        )
        self.assertEqual(
            memory["open_questions"],
            [{"event_id": "reply-1", "speaker": "Architect", "text": "Should play chatter be promoted?"}],
        )
        memory_blob = json.dumps(memory, ensure_ascii=False)
        self.assertIn("resident sessions explicit", memory_blob)
        self.assertNotIn("private moderator prompt", memory_blob)
        self.assertNotIn("leak review-only note", memory_blob)
        self.assertNotIn("private status", memory_blob)

    def test_render_live_meeting_memory_writes_human_readable_shared_records(self):
        memory = {
            "meeting_id": "resident-m1",
            "topic": "Resident Memory",
            "official_event_count": 1,
            "last_official_event_id": "reply-1",
            "rolling_summary": [
                {
                    "event_id": "reply-1",
                    "created_at": "2026-05-22T01:02:03+00:00",
                    "speaker": "Architect",
                    "role_id": "architect",
                    "summary": "We should keep resident sessions explicit.",
                }
            ],
            "decisions": [{"event_id": "reply-1", "speaker": "Architect", "text": "Keep resident sessions explicit."}],
            "open_questions": [{"event_id": "reply-1", "speaker": "Architect", "text": "Should play chatter be promoted?"}],
            "action_items": [{"event_id": "reply-1", "speaker": "Architect", "text": "Add shared memory artifacts."}],
        }

        rolling = render_rolling_summary(memory)
        questions = render_open_questions(memory)
        actions = render_action_items(memory)

        self.assertIn("# Rolling Summary", rolling)
        self.assertIn("We should keep resident sessions explicit.", rolling)
        self.assertIn("## Decisions", rolling)
        self.assertIn("Keep resident sessions explicit.", rolling)
        self.assertIn("# Open Questions", questions)
        self.assertIn("Should play chatter be promoted?", questions)
        self.assertIn("# Action Items", actions)
        self.assertIn("Add shared memory artifacts.", actions)

    def test_projected_live_meeting_memory_reads_full_log_without_writing_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            reply = append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "resident-m1",
                    "actor_id": "agent-a",
                    "display_name": "Agent A",
                    "content": "Old official answer beyond tail.\nAction: Preserve full log memory.",
                },
            )
            for index in range(201):
                append_live_event(meeting_dir, {"kind": "status", "content": f"tail filler {index}"})

            self.assertNotIn(reply["id"], [event["id"] for event in read_live_events(meeting_dir)])

            artifacts = projected_live_meeting_memory_artifacts(meeting_dir, meeting={"meeting_id": "resident-m1", "topic": "Runtime"})

            self.assertIn("Old official answer beyond tail.", artifacts["shared_memory/rolling-summary.md"])
            self.assertIn("Preserve full log memory.", artifacts["shared_memory/action-items.md"])
            self.assertFalse((meeting_dir / "shared_memory" / "rolling-summary.md").exists())
            self.assertFalse((meeting_dir / "shared_memory" / "index.json").exists())

    def test_write_live_meeting_memory_artifacts_rewrites_idempotently(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "resident-m1",
                    "actor_id": "agent-a",
                    "display_name": "Agent A",
                    "content": "Official answer.\nOpen question: Is rewrite idempotent?",
                },
            )

            first = write_live_meeting_memory_artifacts(meeting_dir, meeting={"meeting_id": "resident-m1"})
            first_index = (meeting_dir / "shared_memory" / "index.json").read_text(encoding="utf-8")
            time.sleep(0.01)
            second = write_live_meeting_memory_artifacts(meeting_dir, meeting={"meeting_id": "resident-m1"})
            second_index = (meeting_dir / "shared_memory" / "index.json").read_text(encoding="utf-8")

            self.assertEqual(first, second)
            self.assertEqual(first_index, second_index)
            self.assertEqual(first["official_event_count"], second["official_event_count"])
            self.assertEqual(first["last_official_event_id"], second["last_official_event_id"])
            self.assertTrue((meeting_dir / "shared_memory" / "rolling-summary.md").exists())
            self.assertTrue((meeting_dir / "shared_memory" / "open-questions.md").exists())
            self.assertTrue((meeting_dir / "shared_memory" / "action-items.md").exists())
            self.assertTrue((meeting_dir / "shared_memory" / "index.json").exists())
            self.assertEqual(
                (meeting_dir / "shared_memory" / "open-questions.md").read_text(encoding="utf-8").count("Is rewrite idempotent?"),
                1,
            )


if __name__ == "__main__":
    unittest.main()
