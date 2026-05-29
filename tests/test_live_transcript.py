import tempfile
import unittest
from pathlib import Path

from agentsassemble.live_transcript import projected_live_transcript_text, render_live_transcript
from agentsassemble.meeting_events import append_live_event, read_live_events


class LiveTranscriptTests(unittest.TestCase):
    def test_render_live_transcript_includes_only_official_record_events_with_metadata(self):
        events = [
            {
                "id": "request-1",
                "kind": "live_agent_turn_request",
                "official_record": False,
                "content": "private moderator request",
            },
            {
                "id": "status-1",
                "kind": "status",
                "official_record": False,
                "content": "working",
            },
            {
                "id": "reply-1",
                "created_at": "2026-05-19T01:02:03+00:00",
                "kind": "message",
                "channel": "official",
                "official_record": True,
                "actor_id": "agent-a",
                "role_id": "architect",
                "display_name": "Agent A",
                "turn_id": "round_1:0:architect",
                "turn_index": 0,
                "source_event_id": "request-1",
                "content": "official reply",
            },
            {
                "id": "empty-official",
                "kind": "message",
                "channel": "official",
                "official_record": True,
                "actor_id": "agent-empty",
                "display_name": "Empty Agent",
                "content": "  ",
            },
            {
                "id": "synthesis-1",
                "kind": "synthesis",
                "channel": "official",
                "official_record": True,
                "actor_id": "moderator",
                "display_name": "Moderator",
                "content": "official synthesis",
            },
        ]

        transcript = render_live_transcript(events, meeting={"meeting_id": "m1", "topic": "Runtime"})

        self.assertIn("# Transcript", transcript)
        self.assertIn("Projected from official live events", transcript)
        self.assertIn("Meeting id: m1", transcript)
        self.assertIn("official reply", transcript)
        self.assertIn("official synthesis", transcript)
        self.assertIn("- Event id: reply-1", transcript)
        self.assertIn("- Created at: 2026-05-19T01:02:03+00:00", transcript)
        self.assertIn("- Actor id: agent-a", transcript)
        self.assertIn("- Role id: architect", transcript)
        self.assertIn("- Turn id: round_1:0:architect", transcript)
        self.assertIn("- Turn index: 0", transcript)
        self.assertIn("- Source event id: request-1", transcript)
        self.assertNotIn("private moderator request", transcript)
        self.assertNotIn("working", transcript)
        self.assertNotIn("Empty Agent", transcript)

    def test_projected_live_transcript_text_uses_full_log_beyond_default_tail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            reply = append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-a",
                    "display_name": "Agent A",
                    "content": "old official reply",
                },
            )
            for index in range(201):
                append_live_event(meeting_dir, {"kind": "status", "content": f"tail filler {index}"})

            self.assertNotIn(reply["id"], [event["id"] for event in read_live_events(meeting_dir)])

            transcript = projected_live_transcript_text(meeting_dir, meeting={"meeting_id": "m1", "live_status": "running"})

            self.assertIn("old official reply", transcript)
            self.assertIn(f"- Event id: {reply['id']}", transcript)
            self.assertFalse((meeting_dir / "transcript.md").exists())

    def test_projected_live_transcript_text_returns_empty_when_no_official_events_exist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            append_live_event(meeting_dir, {"kind": "status", "content": "working"})

            transcript = projected_live_transcript_text(meeting_dir, meeting={"meeting_id": "m1", "live_status": "running"})

            self.assertEqual(transcript, "")
            self.assertFalse((meeting_dir / "transcript.md").exists())

    def test_promoted_context_events_render_in_official_transcript(self):
        events = [
            {
                "id": "lobby-raw",
                "kind": "message",
                "channel": "lobby",
                "official_record": False,
                "content": "unpromoted lobby chatter",
            },
            {
                "id": "promoted-1",
                "created_at": "2026-05-29T00:00:00+00:00",
                "kind": "promoted_context",
                "channel": "official",
                "official_record": True,
                "actor_id": "moderator",
                "source_event_id": "lobby-1",
                "promoted_from": "lobby",
                "promoted_reason": "operator selected",
                "content": "Promoted lobby context.",
            },
        ]

        transcript = render_live_transcript(events, meeting={"meeting_id": "m1"})

        self.assertIn("Promoted lobby context.", transcript)
        self.assertIn("- Promoted from: lobby", transcript)
        self.assertIn("- Promoted reason: operator selected", transcript)
        self.assertIn("- Source event id: lobby-1", transcript)
        self.assertNotIn("unpromoted lobby chatter", transcript)


if __name__ == "__main__":
    unittest.main()
