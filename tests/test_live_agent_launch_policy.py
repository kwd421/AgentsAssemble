import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.live_agent_launch_policy import resident_launch_approval_report


class LiveAgentLaunchPolicyTests(unittest.TestCase):
    def test_local_cli_group_is_credential_free(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "fake-local",
                                "display_name": "Fake Local",
                                "provider_kind": "local_cli",
                                "connection_kind": "local_cli",
                                "command": ["python3", "-c", "print('ok')"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = resident_launch_approval_report(config_path)

        self.assertEqual(report["status"], "ok")
        self.assertFalse(report["approval_required"])
        self.assertFalse(report["approved"])
        self.assertEqual(report["approval_required_count"], 0)
        self.assertEqual(report["agents"][0]["agent_id"], "fake-local")
        self.assertFalse(report["agents"][0]["approval_required"])
        self.assertNotIn("print('ok')", json.dumps(report, ensure_ascii=False))

    def test_real_provider_residents_require_current_operator_approval_without_leaking_private_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "private-live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "claude-live",
                                "provider_kind": "claude_code",
                                "connection_kind": "terminal_session",
                                "command": ["/Users/me/private/bin/claude", "--token", "secret-token"],
                            },
                            {
                                "agent_id": "codex-live",
                                "provider_kind": "codex_live_session",
                                "connection_kind": "live_session",
                                "command": ["codex", "exec"],
                            },
                            {
                                "agent_id": "bridge-live",
                                "provider_kind": "remote_http_bridge",
                                "connection_kind": "remote_bridge",
                                "endpoint": "https://bridge.example.test/private",
                                "auth_ref": "env:BRIDGE_TOKEN",
                            },
                            {
                                "agent_id": "cursor-live",
                                "provider_kind": "cursor",
                                "connection_kind": "terminal_session",
                                "command": ["cursor-agent"],
                            },
                            {
                                "agent_id": "cursor-session-live",
                                "provider_kind": "cursor_live_session",
                                "connection_kind": "live_session",
                                "command": ["cursor-agent"],
                            },
                            {
                                "agent_id": "antigravity-live",
                                "provider_kind": "antigravity_cli",
                                "connection_kind": "self_service",
                                "command": ["antigravity"],
                            },
                            {
                                "agent_id": "grok-live",
                                "provider_kind": "grok_live_session",
                                "connection_kind": "live_session",
                                "command": ["grok"],
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = resident_launch_approval_report(config_path)

        serialized = json.dumps(report, ensure_ascii=False)
        self.assertEqual(report["status"], "approval_required")
        self.assertTrue(report["approval_required"])
        self.assertFalse(report["approved"])
        self.assertEqual(report["approval_required_count"], 7)
        self.assertEqual(
            {agent["agent_id"] for agent in report["agents"]},
            {
                "claude-live",
                "codex-live",
                "bridge-live",
                "cursor-live",
                "cursor-session-live",
                "antigravity-live",
                "grok-live",
            },
        )
        self.assertTrue(all(agent["approval_required"] for agent in report["agents"]))
        self.assertNotIn("/Users/me/private", serialized)
        self.assertNotIn("secret-token", serialized)
        self.assertNotIn("https://bridge.example.test", serialized)
        self.assertNotIn("BRIDGE_TOKEN", serialized)
        self.assertNotIn("private-live-agents.json", serialized)

    def test_current_approval_allows_this_action_without_becoming_public_secret_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "claude-live",
                                "provider_kind": "claude_code",
                                "connection_kind": "terminal_session",
                                "command": ["claude"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = resident_launch_approval_report(config_path, approved=True)

        self.assertEqual(report["status"], "ok")
        self.assertTrue(report["approval_required"])
        self.assertTrue(report["approved"])
        self.assertEqual(report["approval_required_count"], 1)

    def test_diagnostic_runs_do_not_require_real_provider_approval(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "live-agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": [
                            {
                                "agent_id": "bridge-live",
                                "provider_kind": "remote_http_bridge",
                                "connection_kind": "remote_bridge",
                                "endpoint": "http://127.0.0.1:9999",
                                "auth_ref": "literal:smoke-token",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = resident_launch_approval_report(config_path, request={"diagnostic": True})

        self.assertEqual(report["status"], "ok")
        self.assertFalse(report["approval_required"])
        self.assertEqual(report["reason"], "diagnostic")


if __name__ == "__main__":
    unittest.main()
