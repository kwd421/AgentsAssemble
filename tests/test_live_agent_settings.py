import json
import tempfile
import unittest
from pathlib import Path


class LiveAgentSettingsTests(unittest.TestCase):
    def test_update_live_agent_config_poll_interval_updates_only_selected_agent(self):
        from agentsassemble.legacy.live_agent.runtime.settings import update_live_agent_config_poll_interval

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
        from agentsassemble.legacy.live_agent.runtime.settings import update_live_agent_config_poll_interval

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


    def test_update_live_agent_config_options_sets_and_clears_permission_and_fast(self):
        from agentsassemble.legacy.live_agent.runtime.settings import update_live_agent_config_options

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {"agent_id": "agent-a", "command": ["codex"]},
                            {"agent_id": "agent-b", "command": ["codex"]},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = update_live_agent_config_options(
                config_path, "agent-a", permission_option="danger-full-access", fast_mode=True
            )
            updated = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(result["permission_option"], "danger-full-access")
            self.assertIs(result["fast_mode"], True)
            self.assertEqual(updated["agents"][0]["permission_option"], "danger-full-access")
            self.assertIs(updated["agents"][0]["fast_mode"], True)
            self.assertNotIn("permission_option", updated["agents"][1])

            # Empty permission + fast off clears both keys (no stale state).
            update_live_agent_config_options(
                config_path, "agent-a", permission_option="", fast_mode=False
            )
            cleared = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertNotIn("permission_option", cleared["agents"][0])
            self.assertNotIn("fast_mode", cleared["agents"][0])

    def test_update_live_agent_config_options_leaves_unspecified_fields(self):
        from agentsassemble.legacy.live_agent.runtime.settings import update_live_agent_config_options

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {"agents": [{"agent_id": "a", "command": ["codex"], "fast_mode": True}]}
                ),
                encoding="utf-8",
            )
            # Only permission given; fast_mode (None) must be left untouched.
            update_live_agent_config_options(config_path, "a", permission_option="workspace-write")
            data = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(data["agents"][0]["permission_option"], "workspace-write")
            self.assertIs(data["agents"][0]["fast_mode"], True)

            with self.assertRaisesRegex(ValueError, "does not include missing"):
                update_live_agent_config_options(config_path, "missing", fast_mode=True)


if __name__ == "__main__":
    unittest.main()
