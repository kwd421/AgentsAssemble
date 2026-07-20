import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.legacy.meeting.official_turns import LegacyOfficialTurnService
from agentsassemble.legacy.meeting.turn_scheduler import meeting_turn_lock
from agentsassemble.legacy.live_agent.runtime.operations import read_live_agent_operations
from agentsassemble.live_agents import connect_live_agent
from agentsassemble.legacy.meeting.core.events import read_live_events, write_live_state


class LegacyOfficialTurnServiceTests(unittest.TestCase):
    def test_request_writes_private_event_and_prompt_free_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = root / "meetings" / "room-a"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, {"meeting_id": "room-a"})
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "display_name": "Agent A",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                    "meeting_id": "room-a",
                },
            )

            result = LegacyOfficialTurnService(root).request(
                "room-a",
                {
                    "agent_id": "agent-a",
                    "role_id": "critic",
                    "content": "SECRET OFFICIAL TURN PROMPT",
                    "turn_id": "turn-1",
                },
            )
            operation = read_live_agent_operations(root, operation="official_turn.request")[0]
            events = read_live_events(meeting_dir, limit=None)

        self.assertEqual(result["event"]["target_agent_id"], "agent-a")
        self.assertEqual(events[0]["content"], "SECRET OFFICIAL TURN PROMPT")
        self.assertEqual(operation["status"], "success")
        self.assertEqual(operation["details"]["source_event_id"], result["event"]["id"])
        self.assertNotIn("SECRET OFFICIAL TURN PROMPT", json.dumps(operation, ensure_ascii=False))

    def test_sequence_failure_audit_counts_turns_without_prompt_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(ValueError, "Meeting missing was not found"):
                LegacyOfficialTurnService(root).sequence(
                    "missing",
                    {
                        "turns": [{"agent_id": "agent-a", "content": "SECRET SEQUENCE PROMPT"}],
                        "timeout_seconds": 2,
                    },
                )
            operation = read_live_agent_operations(root, operation="official_turn.sequence")[0]

        self.assertEqual(operation["status"], "failed")
        self.assertEqual(operation["details"]["turn_count"], 1)
        self.assertNotIn("SECRET SEQUENCE PROMPT", json.dumps(operation, ensure_ascii=False))

    def test_request_failure_audit_uses_normalized_target_without_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(ValueError, "Meeting missing was not found"):
                LegacyOfficialTurnService(root).request(
                    "missing",
                    {"agent_id": " agent-a ", "content": "SECRET REQUEST PROMPT"},
                )
            operation = read_live_agent_operations(root, operation="official_turn.request")[0]

        self.assertEqual(operation["target_id"], "agent-a")
        self.assertEqual(operation["details"]["target_agent_id"], "agent-a")
        self.assertNotIn("SECRET REQUEST PROMPT", json.dumps(operation, ensure_ascii=False))

    def test_meeting_scheduler_reuses_lock_per_meeting(self) -> None:
        self.assertIs(meeting_turn_lock("room-a"), meeting_turn_lock("room-a"))
        self.assertIsNot(meeting_turn_lock("room-a"), meeting_turn_lock("room-b"))


if __name__ == "__main__":
    unittest.main()
