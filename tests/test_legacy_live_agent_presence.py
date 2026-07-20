import tempfile
import unittest
from pathlib import Path

from agentsassemble.legacy.live_agent.presence import LegacyLiveAgentPresenceService
from agentsassemble.legacy.live_agent.runtime.operations import read_live_agent_operations
from agentsassemble.legacy.live_agent.state import connect_live_agent, read_live_agents


class LegacyLiveAgentPresenceServiceTests(unittest.TestCase):
    def test_register_records_previous_and_current_presence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(root, {"agent_id": "agent-a", "display_name": "Old name"})

            result = LegacyLiveAgentPresenceService(root).register(
                {
                    "agent_id": "agent-a",
                    "display_name": "New name",
                    "provider_kind": "local_cli",
                    "connection_kind": "local_cli",
                }
            )
            operation = read_live_agent_operations(root, operation="live_agent.register")[0]

        self.assertEqual(result["agent"]["display_name"], "New name")
        self.assertEqual(operation["status"], "success")
        self.assertEqual(operation["details"]["previous_status"], "online")
        self.assertEqual(operation["details"]["registered_status"], "online")
        self.assertEqual(operation["details"]["admission_status"], "lobby_only")

    def test_heartbeat_updates_metadata_without_creating_operation_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(root, {"agent_id": "agent-a"})

            result = LegacyLiveAgentPresenceService(root).heartbeat(
                "agent-a",
                {
                    "status": "error",
                    "last_error": "provider unavailable",
                    "last_observed_event_id": "event-7",
                },
            )
            operations = read_live_agent_operations(root)

        self.assertEqual(result["agent"]["status"], "error")
        self.assertEqual(result["agent"]["last_error"], "provider unavailable")
        self.assertEqual(result["agent"]["last_observed_event_id"], "event-7")
        self.assertEqual(operations, [])

    def test_leave_marks_agent_offline_and_records_safe_cursors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(
                root,
                {
                    "agent_id": "agent-a",
                    "meeting_id": "room-a",
                    "last_error": "stale error",
                },
            )

            result = LegacyLiveAgentPresenceService(root).leave(
                "agent-a",
                {
                    "last_observed_event_id": "event-8",
                    "last_observed_live_event_id": "live-4",
                },
            )
            operation = read_live_agent_operations(root, operation="live_agent.leave")[0]
            stored = read_live_agents(root)[0]

        self.assertEqual(result["agent"]["status"], "offline")
        self.assertEqual(stored["last_error"], "")
        self.assertEqual(operation["status"], "success")
        self.assertEqual(operation["details"]["meeting_id"], "room-a")
        self.assertEqual(operation["details"]["last_observed_event_id"], "event-8")
        self.assertEqual(operation["details"]["last_observed_live_event_id"], "live-4")

    def test_unknown_leave_records_failure_without_creating_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(ValueError, "was not found"):
                LegacyLiveAgentPresenceService(root).leave("missing", {})
            operation = read_live_agent_operations(root, operation="live_agent.leave")[0]

        self.assertEqual(operation["status"], "failed")
        self.assertEqual(operation["target_id"], "missing")


if __name__ == "__main__":
    unittest.main()
