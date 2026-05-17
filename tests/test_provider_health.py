import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.provider_health import provider_health_report


class ProviderHealthTests(unittest.TestCase):
    def test_provider_health_reports_ok_without_running_commands_or_network(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {"id": "mock-provider", "kind": "mock", "display_name": "Mock"},
                            {
                                "id": "local-model",
                                "kind": "local_openai_compatible",
                                "display_name": "LM Studio",
                                "endpoint": "http://127.0.0.1:1234/v1",
                            },
                            {
                                "id": "cli-provider",
                                "kind": "local_cli",
                                "display_name": "Local CLI",
                                "command": ["fake-agent", "--json"],
                            },
                        ],
                        "permission_profiles": [
                            {"id": "meeting", "meeting_read": True, "official_turn": True}
                        ],
                        "agent_bindings": [
                            {
                                "agent_id": "mock-agent",
                                "role_id": "lore_lawyer",
                                "provider_id": "mock-provider",
                                "permission_profile_id": "meeting",
                            },
                            {
                                "agent_id": "cli-agent",
                                "role_id": "show_me_the_feats",
                                "provider_id": "cli-provider",
                                "permission_profile_id": "meeting",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            resolved_commands = []

            def resolver(command):
                resolved_commands.append(command)
                return "/usr/local/bin/fake-agent" if command == "fake-agent" else None

            report = provider_health_report(config_path, command_resolver=resolver)

            self.assertEqual(report["status"], "ok")
            self.assertEqual(
                report["summary"],
                {
                    "providers": 3,
                    "failed_providers": 0,
                    "bindings": 2,
                    "failed_bindings": 0,
                    "checks_failed": 0,
                    "warnings": 0,
                },
            )
            self.assertEqual(resolved_commands, ["fake-agent"])
            providers = {provider["provider_id"]: provider for provider in report["providers"]}
            self.assertEqual(providers["local-model"]["status"], "ok")
            self.assertEqual(providers["cli-provider"]["command_path"], "/usr/local/bin/fake-agent")

    def test_provider_health_reports_missing_auth_planned_kinds_bad_commands_and_binding_errors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "claude",
                                "kind": "anthropic",
                                "display_name": "Claude API",
                                "auth_ref": "env:AGENTSASSEMBLE_TEST_MISSING_KEY",
                            },
                            {
                                "id": "cursor",
                                "kind": "cursor",
                                "display_name": "Cursor",
                            },
                            {
                                "id": "bad-cli",
                                "kind": "local_cli",
                                "display_name": "Missing CLI",
                                "command": ["missing-agent"],
                            },
                        ],
                        "permission_profiles": [
                            {"id": "meeting", "meeting_read": True, "official_turn": True},
                            {"id": "unsafe", "implementation": True, "filesystem_write": True},
                        ],
                        "agent_bindings": [
                            {
                                "agent_id": "claude-agent",
                                "role_id": "lore_lawyer",
                                "provider_id": "claude",
                                "permission_profile_id": "meeting",
                            },
                            {
                                "agent_id": "cursor-agent",
                                "role_id": "show_me_the_feats",
                                "provider_id": "cursor",
                                "permission_profile_id": "unsafe",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = provider_health_report(config_path, command_resolver=lambda command: None)

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["summary"]["providers"], 3)
            self.assertEqual(report["summary"]["failed_providers"], 3)
            self.assertGreaterEqual(report["summary"]["failed_bindings"], 2)
            providers = {provider["provider_id"]: provider for provider in report["providers"]}
            self.assertIn(
                {
                    "id": "auth_ref",
                    "status": "failed",
                    "message": "Required auth_ref is not available.",
                },
                providers["claude"]["checks"],
            )
            self.assertIn(
                {
                    "id": "provider_kind",
                    "status": "failed",
                    "message": "Provider kind cursor is planned, not available for execution.",
                },
                providers["cursor"]["checks"],
            )
            self.assertIn(
                {"id": "command", "status": "failed", "message": "Command not found: missing-agent"},
                providers["bad-cli"]["checks"],
            )

    def test_provider_health_reports_duplicate_ids_and_secret_permissions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {"id": "dup-provider", "kind": "mock", "display_name": "Mock A"},
                            {"id": "dup-provider", "kind": "mock", "display_name": "Mock B"},
                        ],
                        "permission_profiles": [
                            {"id": "secret-meeting", "secrets": True},
                            {"id": "secret-meeting", "secrets": True},
                        ],
                        "agent_bindings": [
                            {
                                "agent_id": "dup-agent",
                                "role_id": "lore_lawyer",
                                "provider_id": "dup-provider",
                                "permission_profile_id": "secret-meeting",
                            },
                            {
                                "agent_id": "dup-agent",
                                "role_id": "lore_lawyer",
                                "provider_id": "dup-provider",
                                "permission_profile_id": "secret-meeting",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = provider_health_report(config_path)

            self.assertEqual(report["status"], "failed")
            self.assertIn(
                {"id": "provider_ids", "status": "failed", "message": "Duplicate provider ids: dup-provider"},
                report["checks"],
            )
            self.assertIn(
                {"id": "permission_ids", "status": "failed", "message": "Duplicate permission profile ids: secret-meeting"},
                report["checks"],
            )
            self.assertIn(
                {"id": "agent_ids", "status": "failed", "message": "Duplicate agent ids: dup-agent"},
                report["checks"],
            )
            self.assertIn(
                {"id": "role_bindings", "status": "failed", "message": "Duplicate role bindings: lore_lawyer"},
                report["checks"],
            )
            self.assertIn(
                {
                    "id": "secrets",
                    "status": "failed",
                    "message": "Agent dup-agent requests secret access during a meeting-only run.",
                },
                report["bindings"][0]["checks"],
            )

    def test_provider_health_does_not_leak_literal_auth_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "gemini",
                                "kind": "gemini",
                                "display_name": "Gemini",
                                "auth_ref": "literal:super-secret-provider-token",
                            }
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [],
                    }
                ),
                encoding="utf-8",
            )

            report = provider_health_report(config_path)

            self.assertEqual(report["status"], "ok")
            self.assertNotIn("super-secret-provider-token", json.dumps(report))

    def test_provider_health_returns_failed_report_for_invalid_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text("[]", encoding="utf-8")

            report = provider_health_report(config_path)

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["summary"]["checks_failed"], 1)
            self.assertEqual(report["checks"][0]["id"], "config_load")

    def test_provider_health_checks_environment_auth_presence_without_revealing_value(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "grok",
                                "kind": "grok",
                                "display_name": "Grok",
                                "auth_ref": "env:AGENTSASSEMBLE_TEST_XAI_KEY",
                            }
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [],
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"AGENTSASSEMBLE_TEST_XAI_KEY": "secret-xai-value"}):
                report = provider_health_report(config_path)

            self.assertEqual(report["status"], "ok")
            self.assertNotIn("secret-xai-value", json.dumps(report))

    def test_provider_health_does_not_read_environment_secret_values(self):
        class PresenceOnlyEnv:
            def __contains__(self, key):
                return key == "AGENTSASSEMBLE_TEST_ANTHROPIC_KEY"

            def get(self, key, default=None):
                raise AssertionError("provider health must not read secret values")

            def __getitem__(self, key):
                raise AssertionError("provider health must not read secret values")

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "claude",
                                "kind": "anthropic",
                                "display_name": "Claude",
                                "auth_ref": "env:AGENTSASSEMBLE_TEST_ANTHROPIC_KEY",
                            }
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [],
                    }
                ),
                encoding="utf-8",
            )

            with patch("agentsassemble.provider_health.os.environ", PresenceOnlyEnv()):
                with patch("agentsassemble.adapters.http_llm.os.environ", PresenceOnlyEnv()):
                    report = provider_health_report(config_path)

            self.assertEqual(report["status"], "ok")

    def test_provider_health_does_not_construct_provider_adapters_for_binding_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "cli",
                                "kind": "local_cli",
                                "display_name": "Local CLI",
                                "command": ["fake-agent"],
                            }
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [
                            {
                                "agent_id": "cli-agent",
                                "role_id": "lore_lawyer",
                                "provider_id": "cli",
                                "permission_profile_id": "meeting",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch("agentsassemble.adapters.registry.LocalCliAdapter", side_effect=AssertionError("no adapter construction")):
                report = provider_health_report(config_path, command_resolver=lambda command: "/usr/local/bin/fake-agent")

            self.assertEqual(report["status"], "ok")

    def test_provider_health_reports_malformed_endpoint_and_auth_ref_types_without_crashing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "bridge",
                                "kind": "remote_http_bridge",
                                "display_name": "Bridge",
                                "endpoint": {"url": "http://example.test"},
                                "auth_ref": ["env:BRIDGE_TOKEN"],
                            }
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [
                            {
                                "agent_id": "bridge-agent",
                                "role_id": "lore_lawyer",
                                "provider_id": "bridge",
                                "permission_profile_id": "meeting",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = provider_health_report(config_path)

            self.assertEqual(report["status"], "failed")
            provider = report["providers"][0]
            self.assertIn(
                {"id": "endpoint", "status": "failed", "message": "Endpoint must be a string."},
                provider["checks"],
            )

    def test_provider_health_reports_malformed_provider_kind_without_crashing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "bad-kind",
                                "kind": ["mock"],
                                "display_name": "Bad Kind",
                            }
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [],
                    }
                ),
                encoding="utf-8",
            )

            report = provider_health_report(config_path)

            self.assertEqual(report["status"], "failed")
            self.assertIn(
                {"id": "provider_kind", "status": "failed", "message": "Provider kind must be a string."},
                report["providers"][0]["checks"],
            )

    def test_provider_health_reports_malformed_binding_ids_without_crashing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [{"id": "mock-provider", "kind": "mock", "display_name": "Mock"}],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [
                            {
                                "agent_id": "bad-binding",
                                "role_id": "lore_lawyer",
                                "provider_id": ["mock-provider"],
                                "permission_profile_id": ["meeting"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = provider_health_report(config_path)

            self.assertEqual(report["status"], "failed")
            binding = report["bindings"][0]
            self.assertIn(
                {"id": "provider_defined", "status": "failed", "message": "Provider id must be a string."},
                binding["checks"],
            )
            self.assertIn(
                {"id": "permission_defined", "status": "failed", "message": "Permission profile id must be a string."},
                binding["checks"],
            )


if __name__ == "__main__":
    unittest.main()
