import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.live_agent_preflight import preflight_live_agent_config


class LiveAgentPreflightTests(unittest.TestCase):
    def test_preflight_reports_ok_without_running_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "server": "http://room.local",
                        "agents": [
                            {
                                "agent_id": "alpha",
                                "display_name": "Alpha",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": ["python3", "-c", "print('should not run')"],
                            },
                            {
                                "agent_id": "beta",
                                "provider_kind": "local_cli",
                                "connection_kind": "live_session",
                                "command": ["/usr/bin/python3", "-u", "fake.py"],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            def resolver(command):
                calls.append(command)
                return {"/usr/bin/python3": "/usr/bin/python3", "python3": "/opt/bin/python3"}.get(command)

            report = preflight_live_agent_config(config_path, command_resolver=resolver)

            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["config_path"], str(config_path))
            self.assertEqual(report["server"], "http://room.local")
            self.assertEqual(report["summary"], {"agents": 2, "failed_agents": 0, "checks_failed": 0})
            self.assertEqual(calls, ["python3", "/usr/bin/python3"])
            self.assertEqual([agent["status"] for agent in report["agents"]], ["ok", "ok"])
            self.assertEqual(report["agents"][0]["command_path"], "/opt/bin/python3")

    def test_preflight_reports_duplicate_ids_missing_commands_and_unsupported_connections(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "dup",
                                "provider_kind": "claude_code",
                                "connection_kind": "remote_bridge",
                                "command": ["missing-claude"],
                            },
                            {
                                "agent_id": "dup",
                                "provider_kind": "gemini",
                                "connection_kind": "local_cli",
                                "command": ["missing-gemini"],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = preflight_live_agent_config(config_path, command_resolver=lambda command: None)

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["summary"], {"agents": 2, "failed_agents": 2, "checks_failed": 4})
            self.assertIn(
                {"id": "agent_ids", "status": "failed", "message": "Duplicate agent ids: dup"},
                report["checks"],
            )
            first = report["agents"][0]
            self.assertEqual(first["status"], "failed")
            self.assertIn(
                {
                    "id": "connection_kind",
                    "status": "failed",
                    "message": "Resident groups support local_cli and live_session connections.",
                },
                first["checks"],
            )
            self.assertIn(
                {"id": "command", "status": "failed", "message": "Command not found: missing-claude"},
                first["checks"],
            )

    def test_preflight_reports_missing_command_per_agent_without_hiding_other_checks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "dup",
                                "provider_kind": "claude_code",
                                "connection_kind": "remote_bridge",
                            },
                            {
                                "agent_id": "dup",
                                "provider_kind": "gemini",
                                "connection_kind": "local_cli",
                                "command": [],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = preflight_live_agent_config(config_path)

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["summary"], {"agents": 2, "failed_agents": 2, "checks_failed": 4})
            self.assertIn(
                {"id": "agent_ids", "status": "failed", "message": "Duplicate agent ids: dup"},
                report["checks"],
            )
            self.assertIn(
                {"id": "command", "status": "failed", "message": "Command is empty."},
                report["agents"][0]["checks"],
            )
            self.assertIn(
                {
                    "id": "connection_kind",
                    "status": "failed",
                    "message": "Resident groups support local_cli and live_session connections.",
                },
                report["agents"][0]["checks"],
            )
            self.assertIn(
                {"id": "command", "status": "failed", "message": "Command is empty."},
                report["agents"][1]["checks"],
            )

    def test_preflight_command_resolution_rejects_directories_and_non_executable_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            path_tool = bin_dir / "path-tool"
            path_tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            path_tool.chmod(0o755)
            relative_tool = root / "relative-tool"
            relative_tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            relative_tool.chmod(0o755)
            non_executable = root / "not-executable"
            non_executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            non_executable.chmod(0o644)
            executable_dir = root / "exec-dir"
            executable_dir.mkdir()
            executable_dir.chmod(0o755)
            config_path = root / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {"agent_id": "path", "command": ["path-tool"]},
                            {"agent_id": "relative", "command": ["./relative-tool"]},
                            {"agent_id": "file", "command": [str(non_executable)]},
                            {"agent_id": "dir", "command": [str(executable_dir)]},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            old_cwd = os.getcwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, {"PATH": str(bin_dir)}):
                    report = preflight_live_agent_config(config_path)
            finally:
                os.chdir(old_cwd)

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["summary"], {"agents": 4, "failed_agents": 2, "checks_failed": 2})
            by_id = {agent["agent_id"]: agent for agent in report["agents"]}
            self.assertEqual(by_id["path"]["status"], "ok")
            self.assertEqual(by_id["relative"]["status"], "ok")
            self.assertEqual(by_id["file"]["status"], "failed")
            self.assertEqual(by_id["dir"]["status"], "failed")

    def test_preflight_applies_server_override_like_run_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "server": "http://config.local",
                        "agents": [{"agent_id": "alpha", "command": ["python3"]}],
                    }
                ),
                encoding="utf-8",
            )

            report = preflight_live_agent_config(
                config_path,
                server_override="http://override.local",
                command_resolver=lambda command: "/usr/bin/python3",
            )

            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["server"], "http://override.local")

    def test_preflight_returns_failed_report_for_unreadable_config(self):
        missing = Path("/dev/null/agentsassemble-missing/live-agents.json")

        report = preflight_live_agent_config(missing)

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["summary"], {"agents": 0, "failed_agents": 0, "checks_failed": 1})
        self.assertEqual(report["checks"][0]["id"], "config_load")
        self.assertEqual(report["checks"][0]["status"], "failed")

    def test_preflight_fails_when_agents_list_has_no_valid_agent_objects(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(json.dumps({"agents": ["not-an-agent"]}), encoding="utf-8")

            report = preflight_live_agent_config(config_path)

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["summary"], {"agents": 0, "failed_agents": 0, "checks_failed": 1})
            self.assertEqual(report["checks"][0]["id"], "config_load")


if __name__ == "__main__":
    unittest.main()
