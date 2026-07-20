import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.cli import _close_command_runner, _command_runner_for_config
from agentsassemble.legacy.live_agent.runtime.preflight import preflight_live_agent_config
from agentsassemble.live_agent_runner import load_group_configs


class LiveAgentWorkspacePathTests(unittest.TestCase):
    def test_load_group_config_preserves_workspace_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "codex-a",
                                "display_name": "Codex A",
                                "provider_kind": "codex_live_session",
                                "connection_kind": "live_session",
                                "workspace_path": str(workspace),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            config = load_group_configs(config_path)[0]

            self.assertEqual(config.workspace_path, str(workspace))

    def test_provider_command_runner_uses_configured_workspace_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "cursor-a",
                                "display_name": "Cursor A",
                                "provider_kind": "cursor_live_session",
                                "connection_kind": "live_session",
                                "workspace_path": str(workspace),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            config = load_group_configs(config_path)[0]
            runner = _command_runner_for_config(config)
            try:
                self.assertEqual(runner.cwd, workspace)
                self.assertEqual(runner.workspace_dir, workspace)
            finally:
                _close_command_runner(runner)
            self.assertTrue(workspace.exists())

    def test_preflight_rejects_missing_workspace_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_workspace = Path(temp_dir) / "missing-project"
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "grok-a",
                                "display_name": "Grok A",
                                "provider_kind": "grok_live_session",
                                "connection_kind": "live_session",
                                "workspace_path": str(missing_workspace),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = preflight_live_agent_config(
                config_path,
                command_resolver=lambda command: f"/usr/bin/{command}",
            )

            agent = report["agents"][0]
            workspace_checks = [check for check in agent["checks"] if check["id"] == "workspace_path"]
            self.assertEqual(workspace_checks[0]["status"], "failed")
            self.assertIn("Workspace folder was not found", workspace_checks[0]["message"])


if __name__ == "__main__":
    unittest.main()
