import tempfile
import unittest
from pathlib import Path

from agentsassemble.legacy_live_agent_engagement import LegacyLiveAgentEngagementService
from agentsassemble.live_agent_operations import read_live_agent_operations
from agentsassemble.live_agents import connect_live_agent, read_live_agents


class LegacyLiveAgentEngagementServiceTests(unittest.TestCase):
    def test_update_records_previous_and_current_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(root, {"agent_id": "agent-a", "engagement_mode": "always"})

            result = LegacyLiveAgentEngagementService(root).update(
                "agent-a",
                {"engagement_mode": "watch"},
            )
            operation = read_live_agent_operations(root, operation="engagement.update")[0]
            stored = read_live_agents(root)[0]

        self.assertEqual(result["agent"]["engagement_mode"], "watch")
        self.assertEqual(stored["engagement_mode"], "watch")
        self.assertEqual(operation["status"], "success")
        self.assertEqual(operation["details"]["previous_engagement_mode"], "always")
        self.assertEqual(operation["details"]["engagement_mode"], "watch")

    def test_invalid_mode_records_failure_without_mutating_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(root, {"agent_id": "agent-a", "engagement_mode": "always"})

            with self.assertRaisesRegex(ValueError, "Unknown engagement mode"):
                LegacyLiveAgentEngagementService(root).update(
                    "agent-a",
                    {"engagement_mode": "shout_forever"},
                )
            operation = read_live_agent_operations(root, operation="engagement.update")[0]
            stored = read_live_agents(root)[0]

        self.assertEqual(stored["engagement_mode"], "always")
        self.assertEqual(operation["status"], "failed")
        self.assertEqual(operation["details"]["engagement_mode"], "shout_forever")

    def test_invalid_json_audit_contains_no_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            LegacyLiveAgentEngagementService(root).record_invalid_json("agent-a")
            operation = read_live_agent_operations(root, operation="engagement.update")[0]

        self.assertEqual(operation["status"], "failed")
        self.assertEqual(operation["target_id"], "agent-a")
        self.assertEqual(operation["details"], {})


if __name__ == "__main__":
    unittest.main()
