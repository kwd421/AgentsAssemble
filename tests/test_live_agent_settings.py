import json
import tempfile
import unittest
from pathlib import Path


class LiveAgentSettingsTests(unittest.TestCase):
    def test_update_live_agent_config_poll_interval_updates_only_selected_agent(self):
        from agentsassemble.live_agent_settings import update_live_agent_config_poll_interval

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "server": "http://room.local",
                        "poll_interval": 2,
                        "heartbeat_interval": 30,
                        "agents": [
                            {"agent_id": "agent-a", "command": ["fake-a"]},
                            {"agent_id": "agent-b", "command": ["fake-b"], "poll_interval": 1},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = update_live_agent_config_poll_interval(config_path, "agent-a", 0.5)
            updated = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result["agent_id"], "agent-a")
        self.assertEqual(result["poll_interval"], 0.5)
        self.assertEqual(updated["poll_interval"], 2)
        self.assertEqual(updated["agents"][0]["poll_interval"], 0.5)
        self.assertEqual(updated["agents"][1]["poll_interval"], 1)

    def test_update_live_agent_config_poll_interval_rejects_missing_agent_and_invalid_value(self):
        from agentsassemble.live_agent_settings import update_live_agent_config_poll_interval

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps({"agents": [{"agent_id": "agent-a", "command": ["fake"]}]}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "finite non-negative"):
                update_live_agent_config_poll_interval(config_path, "agent-a", -1)
            with self.assertRaisesRegex(ValueError, "does not include missing-agent"):
                update_live_agent_config_poll_interval(config_path, "missing-agent", 0.25)


if __name__ == "__main__":
    unittest.main()
