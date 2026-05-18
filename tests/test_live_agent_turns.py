import tempfile
import unittest
from pathlib import Path

from agentsassemble.live_agent_turns import wait_for_official_turn_reply
from agentsassemble.meeting_events import append_live_event, read_live_events


class LiveAgentTurnsTests(unittest.TestCase):
    def test_wait_returns_matching_official_reply_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            request = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "official turn",
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-b",
                    "source_event_id": request["id"],
                    "content": "wrong agent",
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-a",
                    "source_event_id": "other-request",
                    "content": "wrong request",
                },
            )
            reply = append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-a",
                    "source_event_id": request["id"],
                    "content": "verified official reply",
                },
            )

            result = wait_for_official_turn_reply(
                meeting_dir,
                agent_id="agent-a",
                source_event_id=str(request["id"]),
                timeout_seconds=0,
                poll_interval=0,
            )

            self.assertEqual(result["status"], "answered")
            self.assertEqual(result["reply_event"]["id"], reply["id"])
            self.assertEqual(result["source_event_id"], request["id"])

    def test_wait_uses_full_live_event_log_beyond_default_tail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            request = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "official turn",
                },
            )
            reply = append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "m1",
                    "actor_id": "agent-a",
                    "source_event_id": request["id"],
                    "content": "old official reply",
                },
            )
            for index in range(201):
                append_live_event(meeting_dir, {"kind": "status", "content": f"tail filler {index}"})

            self.assertNotIn(reply["id"], [event["id"] for event in read_live_events(meeting_dir)])

            result = wait_for_official_turn_reply(
                meeting_dir,
                agent_id="agent-a",
                source_event_id=str(request["id"]),
                timeout_seconds=0,
                poll_interval=0,
            )

            self.assertEqual(result["status"], "answered")
            self.assertEqual(result["reply_event"]["id"], reply["id"])

    def test_wait_timeout_does_not_fabricate_reply(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            request = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "m1",
                    "target_agent_id": "agent-a",
                    "content": "official turn",
                },
            )

            result = wait_for_official_turn_reply(
                meeting_dir,
                agent_id="agent-a",
                source_event_id=str(request["id"]),
                timeout_seconds=0,
                poll_interval=0,
            )

            self.assertEqual(result["status"], "timeout")
            self.assertIsNone(result["reply_event"])
            self.assertEqual(result["source_event_id"], request["id"])


if __name__ == "__main__":
    unittest.main()
