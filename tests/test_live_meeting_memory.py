import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.live_meeting_memory import (
    build_live_meeting_memory,
    load_live_meeting_memory_context,
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

    def test_load_live_meeting_memory_context_prefers_index_over_stale_embedded_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            shared_dir = meeting_dir / "shared_memory"
            shared_dir.mkdir(parents=True)
            (shared_dir / "index.json").write_text(
                json.dumps(
                    {
                        "official_event_count": 2,
                        "last_official_event_id": "fresh-reply",
                        "rolling_summary": [
                            {"event_id": "fresh-reply", "speaker": "Architect", "summary": "Fresh index memory."}
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            memory = load_live_meeting_memory_context(
                meeting_dir,
                meeting={
                    "shared_memory": {
                        "official_event_count": 1,
                        "last_official_event_id": "stale-reply",
                        "rolling_summary": [
                            {"event_id": "stale-reply", "speaker": "Architect", "summary": "Stale embedded memory."}
                        ],
                    }
                },
            )

            self.assertEqual(memory["official_event_count"], 2)
            self.assertEqual(memory["last_official_event_id"], "fresh-reply")
            self.assertEqual(memory["rolling_summary"][0]["summary"], "Fresh index memory.")
            self.assertNotIn("Stale embedded memory.", json.dumps(memory, ensure_ascii=False))

    def test_load_live_meeting_memory_context_projects_newer_official_events_over_stale_index_without_writing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            shared_dir = meeting_dir / "shared_memory"
            shared_dir.mkdir(parents=True)
            (shared_dir / "index.json").write_text(
                json.dumps(
                    {
                        "official_event_count": 1,
                        "last_official_event_id": "stale-reply",
                        "rolling_summary": [
                            {"event_id": "stale-reply", "speaker": "Architect", "summary": "Stale file memory."}
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            original_index = (shared_dir / "index.json").read_text(encoding="utf-8")
            append_live_event(
                meeting_dir,
                {
                    "id": "private-request",
                    "kind": "live_agent_turn_request",
                    "channel": "system",
                    "official_record": False,
                    "content": "private prompt must stay out",
                },
            )
            reply = append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "channel": "official",
                    "official_record": True,
                    "actor_id": "agent-a",
                    "display_name": "Architect",
                    "content": "Fresh official memory.\nAction: Keep room context current.",
                },
            )

            memory = load_live_meeting_memory_context(meeting_dir, meeting={"meeting_id": "resident-m1", "topic": "Runtime"})

            self.assertEqual(memory["official_event_count"], 1)
            self.assertEqual(memory["last_official_event_id"], reply["id"])
            memory_text = json.dumps(memory, ensure_ascii=False)
            self.assertIn("Fresh official memory.", memory_text)
            self.assertIn("Keep room context current.", memory_text)
            self.assertNotIn("Stale file memory.", memory_text)
            self.assertNotIn("private prompt must stay out", memory_text)
            self.assertEqual((shared_dir / "index.json").read_text(encoding="utf-8"), original_index)

    def test_load_live_meeting_memory_context_projects_official_log_when_matching_index_contains_untrusted_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            reply = append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "channel": "official",
                    "official_record": True,
                    "actor_id": "agent-a",
                    "display_name": "Architect",
                    "content": "Official room memory.\nAction: Trust the official event log.",
                },
            )
            shared_dir = meeting_dir / "shared_memory"
            shared_dir.mkdir(parents=True)
            (shared_dir / "index.json").write_text(
                json.dumps(
                    {
                        "official_event_count": 1,
                        "last_official_event_id": reply["id"],
                        "rolling_summary": [
                            {
                                "event_id": reply["id"],
                                "speaker": "Architect",
                                "summary": "private provider output leak",
                            }
                        ],
                        "action_items": [
                            {
                                "event_id": reply["id"],
                                "speaker": "Architect",
                                "text": "private prompt leak",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            original_index = (shared_dir / "index.json").read_text(encoding="utf-8")

            memory = load_live_meeting_memory_context(meeting_dir, meeting={"meeting_id": "resident-m1", "topic": "Runtime"})

            memory_text = json.dumps(memory, ensure_ascii=False)
            self.assertEqual(memory["official_event_count"], 1)
            self.assertEqual(memory["last_official_event_id"], reply["id"])
            self.assertIn("Official room memory.", memory_text)
            self.assertIn("Trust the official event log.", memory_text)
            self.assertNotIn("private provider output leak", memory_text)
            self.assertNotIn("private prompt leak", memory_text)
            self.assertEqual((shared_dir / "index.json").read_text(encoding="utf-8"), original_index)

    def test_load_live_meeting_memory_context_reuses_cached_official_projection_until_log_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "channel": "official",
                    "official_record": True,
                    "actor_id": "agent-a",
                    "display_name": "Architect",
                    "content": "Initial official memory.",
                },
            )

            with patch("agentsassemble.live_meeting_memory.read_live_events", wraps=read_live_events) as read_mock:
                first = load_live_meeting_memory_context(meeting_dir, meeting={"meeting_id": "resident-m1", "topic": "Runtime"})
                second = load_live_meeting_memory_context(meeting_dir, meeting={"meeting_id": "resident-m1", "topic": "Runtime"})

                self.assertEqual(read_mock.call_count, 1)
                self.assertEqual(first, second)

                append_live_event(
                    meeting_dir,
                    {
                        "kind": "message",
                        "channel": "official",
                        "official_record": True,
                        "actor_id": "agent-b",
                        "display_name": "Planner",
                        "content": "Second official memory.",
                    },
                )
                third = load_live_meeting_memory_context(meeting_dir, meeting={"meeting_id": "resident-m1", "topic": "Runtime"})

                self.assertEqual(read_mock.call_count, 2)
                self.assertEqual(third["official_event_count"], 2)
                self.assertNotEqual(first["last_official_event_id"], third["last_official_event_id"])
                self.assertIn("Second official memory.", json.dumps(third, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
