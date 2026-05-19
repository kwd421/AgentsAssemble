import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.live_agent_preflight import preflight_live_agent_config, resident_config_setup_error
from agentsassemble.live_agent_runner import ResidentAgentConfig


class LiveAgentPreflightTests(unittest.TestCase):
    def resident_config(self, **overrides):
        data = {
            "server": "http://room.local",
            "agent_id": "codex-live",
            "display_name": "Codex Live",
            "provider_kind": "codex_live_session",
            "connection_kind": "live_session",
            "session_id": "",
            "endpoint": "",
            "auth_ref": "",
            "meeting_id": "",
            "engagement_mode": "always",
            "command": ["codex"],
            "timeout_seconds": 30,
            "poll_interval": 0,
            "heartbeat_interval": 30,
            "cooldown": 0,
            "max_chain_depth": 1,
            "max_ticks": 1,
        }
        data.update(overrides)
        return ResidentAgentConfig(**data)

    def test_resident_setup_error_checks_codex_live_session_safety_flags(self):
        errors = []

        error = resident_config_setup_error(
            self.resident_config(),
            command_resolver=lambda command: "/usr/local/bin/codex" if command == "codex" else None,
            codex_capability_checker=lambda command: errors.append(command) or {
                "id": "codex_exec_safety_flags",
                "status": "failed",
                "message": "Codex command does not accept required live-session safety flags.",
            },
        )

        self.assertEqual(error, "Codex command does not accept required live-session safety flags.")
        self.assertEqual(errors, [["/usr/local/bin/codex"]])

    def test_codex_live_agent_example_preflights_with_fake_codex_resolver(self):
        config_path = Path("configs/live-agents.codex-session.example.json")

        report = preflight_live_agent_config(
            config_path,
            command_resolver=lambda command: "/usr/local/bin/codex" if command == "codex" else None,
            codex_capability_checker=lambda command: {
                "id": "codex_exec_safety_flags",
                "status": "ok",
                "message": "Codex exec read-only safety flags are available.",
            },
        )

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["summary"]["agents"], 2)
        self.assertEqual(report["summary"]["checks_failed"], 0)
        self.assertEqual([agent["command"] for agent in report["agents"]], [["codex"], ["codex"]])

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
                                "connection_kind": "manual",
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
                    "message": "Resident groups support local_cli, live_session, and remote_bridge connections.",
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
                                "connection_kind": "manual",
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
                    "message": "Resident groups support local_cli, live_session, and remote_bridge connections.",
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

    def test_preflight_rejects_mixed_non_object_agent_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps({"agents": [{"agent_id": "agent-a", "command": ["python3"]}, "agent-b"]}),
                encoding="utf-8",
            )

            report = preflight_live_agent_config(config_path, command_resolver=lambda command: "/usr/bin/python3")

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["summary"], {"agents": 0, "failed_agents": 0, "checks_failed": 1})
            self.assertEqual(report["checks"][0]["id"], "config_load")
            self.assertEqual(report["checks"][0]["message"], "Each live agent entry must be a JSON object.")

    def test_preflight_accepts_remote_bridge_without_command_and_checks_endpoint_auth_ref(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "friend",
                                "provider_kind": "claude_code",
                                "connection_kind": "remote_bridge",
                                "endpoint": "http://friend.local:8777",
                                "auth_ref": "env:BRIDGE_TOKEN",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"BRIDGE_TOKEN": "available"}, clear=False):
                report = preflight_live_agent_config(config_path, command_resolver=lambda command: None)

            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["summary"], {"agents": 1, "failed_agents": 0, "checks_failed": 0})
            friend = report["agents"][0]
            self.assertEqual(friend["connection_kind"], "remote_bridge")
            self.assertEqual(friend["command"], [])
            self.assertEqual(friend["command_path"], "")
            self.assertIn(
                {"id": "remote_bridge_endpoint", "status": "ok", "message": "Remote bridge endpoint is configured."},
                friend["checks"],
            )
            self.assertIn(
                {"id": "remote_bridge_auth_ref", "status": "ok", "message": "Remote bridge auth_ref is available."},
                friend["checks"],
            )

    def test_preflight_accepts_codex_live_session_with_default_codex_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "codex-live",
                                "provider_kind": "codex_live_session",
                                "connection_kind": "live_session",
                                "session_id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = preflight_live_agent_config(
                config_path,
                command_resolver=lambda command: "/usr/local/bin/codex" if command == "codex" else None,
                codex_capability_checker=lambda command: {
                    "id": "codex_exec_safety_flags",
                    "status": "ok",
                    "message": "Codex exec read-only safety flags are available.",
                },
            )

            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["summary"], {"agents": 1, "failed_agents": 0, "checks_failed": 0})
            agent = report["agents"][0]
            self.assertEqual(agent["provider_kind"], "codex_live_session")
            self.assertEqual(agent["connection_kind"], "live_session")
            self.assertEqual(agent["command"], ["codex"])
            self.assertEqual(agent["command_path"], "/usr/local/bin/codex")
            self.assertIn(
                {
                    "id": "provider_connection_kind",
                    "status": "ok",
                    "message": "codex_live_session uses live_session.",
                },
                agent["checks"],
            )
            self.assertIn(
                {
                    "id": "codex_exec_safety_flags",
                    "status": "ok",
                    "message": "Codex exec read-only safety flags are available.",
                },
                agent["checks"],
            )

    def test_preflight_rejects_codex_live_session_when_safety_flag_probe_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "codex-live",
                                "provider_kind": "codex_live_session",
                                "connection_kind": "live_session",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            probed_commands = []

            def capability_checker(command):
                probed_commands.append(command)
                return {
                    "id": "codex_exec_safety_flags",
                    "status": "failed",
                    "message": "Codex command does not accept required live-session safety flags.",
                }

            report = preflight_live_agent_config(
                config_path,
                command_resolver=lambda command: "/usr/local/bin/codex" if command == "codex" else None,
                codex_capability_checker=capability_checker,
            )

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["summary"], {"agents": 1, "failed_agents": 1, "checks_failed": 1})
            self.assertEqual(probed_commands, [["/usr/local/bin/codex"]])
            self.assertIn(
                {
                    "id": "codex_exec_safety_flags",
                    "status": "failed",
                    "message": "Codex command does not accept required live-session safety flags.",
                },
                report["agents"][0]["checks"],
            )

    def test_default_codex_capability_probe_uses_exec_level_safety_flags_before_resume(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "codex-live",
                                "provider_kind": "codex_live_session",
                                "connection_kind": "live_session",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            def fake_run(command, **kwargs):
                calls.append({"command": command, "kwargs": kwargs})

                class Completed:
                    returncode = 0
                    stdout = "help"
                    stderr = ""

                return Completed()

            report = preflight_live_agent_config(
                config_path,
                command_resolver=lambda command: "/usr/local/bin/codex" if command == "codex" else None,
                codex_command_runner=fake_run,
            )

            self.assertEqual(report["status"], "ok")
            self.assertEqual(calls[0]["command"][:6], ["/usr/local/bin/codex", "exec", "--sandbox", "read-only", "--ignore-rules", "resume"])
            self.assertLess(calls[0]["command"].index("--sandbox"), calls[0]["command"].index("resume"))
            self.assertIn("--skip-git-repo-check", calls[0]["command"])
            self.assertIn("--help", calls[0]["command"])
            self.assertEqual(calls[0]["kwargs"]["timeout"], 10)
            self.assertTrue(calls[0]["kwargs"]["capture_output"])
            self.assertFalse(calls[0]["kwargs"]["check"])

    def test_preflight_rejects_codex_live_session_with_non_codex_executable_before_probe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "codex-live",
                                "provider_kind": "codex_live_session",
                                "connection_kind": "live_session",
                                "command": ["wrapped-codex"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            probed_commands = []

            report = preflight_live_agent_config(
                config_path,
                command_resolver=lambda command: "/usr/local/bin/wrapped-codex" if command == "wrapped-codex" else None,
                codex_capability_checker=lambda command: probed_commands.append(command) or {
                    "id": "codex_exec_safety_flags",
                    "status": "ok",
                    "message": "Codex exec read-only safety flags are available.",
                },
            )

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["summary"], {"agents": 1, "failed_agents": 1, "checks_failed": 1})
            self.assertEqual(probed_commands, [])
            self.assertIn(
                {
                    "id": "codex_command",
                    "status": "failed",
                    "message": "codex_live_session command executable must be named codex.",
                },
                report["agents"][0]["checks"],
            )

    def test_preflight_rejects_codex_live_session_with_global_codex_arguments_before_probe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "codex-live",
                                "provider_kind": "codex_live_session",
                                "connection_kind": "live_session",
                                "command": ["codex", "--dangerously-bypass-approvals-and-sandbox"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            probed_commands = []

            report = preflight_live_agent_config(
                config_path,
                command_resolver=lambda command: "/usr/local/bin/codex" if command == "codex" else None,
                codex_capability_checker=lambda command: probed_commands.append(command) or {
                    "id": "codex_exec_safety_flags",
                    "status": "ok",
                    "message": "Codex exec read-only safety flags are available.",
                },
            )

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["summary"], {"agents": 1, "failed_agents": 1, "checks_failed": 1})
            self.assertEqual(probed_commands, [])
            self.assertIn(
                {
                    "id": "codex_command",
                    "status": "failed",
                    "message": "codex_live_session command must contain only the codex executable.",
                },
                report["agents"][0]["checks"],
            )

    def test_preflight_rejects_codex_live_session_with_local_cli_connection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "codex-live",
                                "provider_kind": "codex_live_session",
                                "connection_kind": "local_cli",
                                "command": ["codex"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = preflight_live_agent_config(
                config_path,
                command_resolver=lambda command: "/usr/local/bin/codex" if command == "codex" else None,
            )

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["summary"], {"agents": 1, "failed_agents": 1, "checks_failed": 1})
            self.assertIn(
                {
                    "id": "provider_connection_kind",
                    "status": "failed",
                    "message": "codex_live_session residents require live_session connection_kind.",
                },
                report["agents"][0]["checks"],
            )

    def test_preflight_rejects_remote_bridge_redacted_auth_placeholder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "friend",
                                "connection_kind": "remote_bridge",
                                "endpoint": "http://friend.local:8777",
                                "auth_ref": "literal:<redacted>",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = preflight_live_agent_config(config_path, command_resolver=lambda command: None)

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["summary"], {"agents": 1, "failed_agents": 1, "checks_failed": 1})
            self.assertIn(
                {
                    "id": "remote_bridge_auth_ref",
                    "status": "failed",
                    "message": "Remote bridge auth_ref is not available.",
                },
                report["agents"][0]["checks"],
            )

    def test_preflight_rejects_remote_bridge_redacted_env_auth_value(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "friend",
                                "connection_kind": "remote_bridge",
                                "endpoint": "http://friend.local:8777",
                                "auth_ref": "env:BRIDGE_TOKEN",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"BRIDGE_TOKEN": "<redacted>"}, clear=False):
                report = preflight_live_agent_config(config_path, command_resolver=lambda command: None)

            self.assertEqual(report["status"], "failed")
            self.assertIn(
                {
                    "id": "remote_bridge_auth_ref",
                    "status": "failed",
                    "message": "Remote bridge auth_ref is not available.",
                },
                report["agents"][0]["checks"],
            )

    def test_preflight_rejects_non_string_remote_bridge_auth_ref(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "friend",
                                "connection_kind": "remote_bridge",
                                "endpoint": "http://friend.local:8777",
                                "auth_ref": ["literal:<redacted>"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = preflight_live_agent_config(config_path, command_resolver=lambda command: None)

            self.assertEqual(report["status"], "failed")
            self.assertIn(
                {
                    "id": "remote_bridge_auth_ref",
                    "status": "failed",
                    "message": "Remote bridge auth_ref is not available.",
                },
                report["agents"][0]["checks"],
            )

    def test_preflight_rejects_remote_bridge_endpoint_with_userinfo_query_or_fragment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "friend",
                                "connection_kind": "remote_bridge",
                                "endpoint": "http://bridge-token@friend.local:8777?secret=1#frag",
                                "auth_ref": "literal:bridge-token",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = preflight_live_agent_config(config_path, command_resolver=lambda command: None)

            self.assertEqual(report["status"], "failed")
            self.assertIn(
                {
                    "id": "remote_bridge_endpoint",
                    "status": "failed",
                    "message": "Remote bridge endpoint must be HTTP(S) without userinfo, query, or fragment.",
                },
                report["agents"][0]["checks"],
            )

    def test_preflight_rejects_malformed_remote_bridge_endpoint_netloc(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for endpoint in ("http://:8777", "http://friend.local:bad", "http://friend.local:99999"):
                config_path = Path(temp_dir) / f"{endpoint.replace('/', '_').replace(':', '_')}.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "agents": [
                                {
                                    "agent_id": "friend",
                                    "connection_kind": "remote_bridge",
                                    "endpoint": endpoint,
                                    "auth_ref": "literal:bridge-token",
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )

                report = preflight_live_agent_config(config_path, command_resolver=lambda command: None)

                self.assertEqual(report["status"], "failed", endpoint)
                self.assertIn(
                    {
                        "id": "remote_bridge_endpoint",
                        "status": "failed",
                        "message": "Remote bridge endpoint must be an HTTP(S) URL with a valid host and port.",
                    },
                    report["agents"][0]["checks"],
                )


if __name__ == "__main__":
    unittest.main()
