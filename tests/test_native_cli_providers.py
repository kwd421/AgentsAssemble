import tempfile
import unittest

from agentsassemble.native_cli_providers import (
    NativeCliProviderSpec,
    UnsupportedNativeCliProvider,
    default_native_cli_provider_specs,
    native_cli_provider_catalog_payload,
    native_cli_provider_spec_from_config,
    native_cli_provider_spec_from_payload,
    validate_native_cli_provider_spec,
)


class NativeCliProviderCatalogTests(unittest.TestCase):
    def test_catalog_builds_all_interactive_default_specs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            specs = {spec.agent_id: spec for spec in default_native_cli_provider_specs(workspace=temp_dir)}

        self.assertEqual(list(specs), ["codex", "antigravity", "grok", "claude"])
        self.assertEqual(specs["codex"].model, "gpt-5.3-codex-spark")
        self.assertEqual(specs["claude"].model, "haiku")
        self.assertEqual(specs["grok"].command, ("grok", "agent", "stdio"))
        self.assertNotIn("-p", specs["claude"].command)
        self.assertNotIn("--print", specs["claude"].command)
        tools_index = specs["claude"].command.index("--tools")
        self.assertEqual(specs["claude"].command[tools_index + 1], "")
        self.assertIn("--safe-mode", specs["claude"].command)

    def test_create_payload_aliases_use_the_same_catalog_definition(self):
        antigravity = native_cli_provider_spec_from_payload(
            {"provider_id": "agy", "display_name": "Agy Friend", "workspace": "."}
        )
        claude = native_cli_provider_spec_from_payload(
            {"provider_kind": "claude_code", "display_name": "Reviewer", "model": "haiku"}
        )

        self.assertEqual(antigravity.provider_kind, "antigravity_live_session")
        self.assertEqual(antigravity.command[0], "agy")
        self.assertEqual(claude.provider_kind, "claude_code")
        self.assertEqual(claude.command[:3], ("claude", "--model", "haiku"))

    def test_smoke_config_can_override_command_without_forking_known_metadata(self):
        spec = native_cli_provider_spec_from_config(
            {
                "id": "codex",
                "display_name": "Test Codex",
                "command": ["fake-codex", "--model", "test-model"],
                "cwd": ".",
            },
            turn_timeout_seconds=7.0,
        )

        self.assertEqual(spec.command, ("fake-codex", "--model", "test-model"))
        self.assertEqual(spec.provider_kind, "codex_live_session")
        self.assertEqual(spec.model, "test-model")
        self.assertEqual(spec.turn_timeout_seconds, 7.0)

    def test_config_preserves_an_intentional_empty_cli_argument(self):
        spec = native_cli_provider_spec_from_config(
            {
                "id": "claude",
                "command": ["claude", "--tools", "", "--safe-mode"],
                "cwd": ".",
            },
            turn_timeout_seconds=30.0,
        )

        self.assertEqual(spec.command, ("claude", "--tools", "", "--safe-mode"))

    def test_real_grok_command_cannot_fall_back_to_pty(self):
        with self.assertRaisesRegex(ValueError, "PTY fallback is disabled"):
            validate_native_cli_provider_spec(
                NativeCliProviderSpec(
                    agent_id="grok",
                    display_name="Grok",
                    provider_kind="grok_live_session",
                    command=("grok", "--no-alt-screen"),
                )
            )

    def test_public_catalog_is_safe_and_unknown_provider_is_clear(self):
        payload = native_cli_provider_catalog_payload()

        self.assertEqual([provider["id"] for provider in payload], ["codex", "antigravity", "grok", "claude"])
        self.assertTrue(all(provider["interactive"] for provider in payload))
        self.assertTrue(all("command" not in provider for provider in payload))
        with self.assertRaises(UnsupportedNativeCliProvider):
            native_cli_provider_spec_from_payload({"provider_id": "unknown"})


if __name__ == "__main__":
    unittest.main()
