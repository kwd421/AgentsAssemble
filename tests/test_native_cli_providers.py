import tempfile
import unittest
from dataclasses import replace

from agentsassemble.providers.launch_specs import (
    NativeCliProviderSpec,
    StoredProviderProfileError,
    UnsupportedNativeCliProvider,
    default_native_cli_provider_specs,
    native_cli_provider_catalog_payload,
    native_cli_provider_definition,
    native_cli_provider_spec_from_config,
    native_cli_provider_spec_from_payload,
    native_cli_provider_spec_from_stored_session_strict,
    validate_native_cli_provider_spec,
)


class NativeCliProviderCatalogTests(unittest.TestCase):
    def test_catalog_builds_all_interactive_default_specs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            specs = {spec.agent_id: spec for spec in default_native_cli_provider_specs(workspace=temp_dir)}

        self.assertEqual(list(specs), ["codex", "antigravity", "grok", "claude"])
        self.assertEqual(specs["codex"].model, "gpt-5.6-luna")
        self.assertEqual(specs["codex"].reasoning_effort, "low")
        self.assertEqual(specs["claude"].model, "claude-haiku-4-5")
        self.assertEqual(specs["grok"].command, ("grok", "--model", "grok-4.5", "agent", "stdio"))
        self.assertEqual(specs["grok"].transport, "acp_stdio")
        self.assertNotIn("-p", specs["claude"].command)
        self.assertNotIn("--print", specs["claude"].command)
        self.assertNotIn("--permission-mode", specs["claude"].command)
        tools_index = specs["claude"].command.index("--tools")
        self.assertEqual(specs["claude"].command[tools_index + 1], "")
        self.assertIn("--safe-mode", specs["claude"].command)

    def test_claude_workspace_write_uses_native_accept_edits_mode(self):
        definition = native_cli_provider_definition("claude")
        self.assertIsNotNone(definition)

        spec = definition.make_selected_spec(
            agent_id="claude-write",
            display_name="Claude Write",
            cwd=".",
            model="claude-haiku-4-5",
            reasoning_effort="low",
            service_tier="default",
            permission_mode="workspace_write",
        )

        mode_index = spec.command.index("--permission-mode")
        self.assertEqual(spec.command[mode_index + 1], "acceptEdits")
        self.assertNotIn("plan", spec.command)

    def test_create_payload_aliases_use_the_same_catalog_definition(self):
        antigravity = native_cli_provider_spec_from_payload(
            {
                "provider_id": "agy",
                "display_name": "Agy Friend",
                "workspace": ".",
                "model": "Gemini 3.5 Flash (Medium)",
                "permission_mode": "meeting_read_only",
            }
        )
        claude = native_cli_provider_spec_from_payload(
            {
                "provider_kind": "claude_code",
                "display_name": "Reviewer",
                "workspace": ".",
                "model": "haiku",
                "reasoning_effort": "high",
                "service_tier": "default",
                "permission_mode": "meeting_read_only",
                "model_selection_kind": "alias",
            }
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
                "model": "test-model",
                "reasoning_effort": "low",
                "service_tier": "default",
                "permission_mode": "meeting_read_only",
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
                "command": ["claude", "--model", "claude-sonnet-4-6", "--tools", "", "--safe-mode"],
                "cwd": ".",
                "model": "claude-sonnet-4-6",
                "reasoning_effort": "low",
                "service_tier": "default",
                "permission_mode": "meeting_read_only",
            },
            turn_timeout_seconds=30.0,
        )

        self.assertEqual(
            spec.command,
            ("claude", "--model", "claude-sonnet-4-6", "--tools", "", "--safe-mode"),
        )

    def test_smoke_config_rejects_missing_or_mismatched_model_profiles(self):
        common = {
            "id": "grok",
            "command": ["grok", "--model", "grok-4.5", "agent", "stdio"],
            "cwd": ".",
            "reasoning_effort": "low",
            "permission_mode": "meeting_read_only",
        }
        with self.assertRaisesRegex(ValueError, "model is required"):
            native_cli_provider_spec_from_config(common, turn_timeout_seconds=30.0)
        with self.assertRaisesRegex(ValueError, "command model does not match"):
            native_cli_provider_spec_from_config(
                {**common, "model": "grok-4"},
                turn_timeout_seconds=30.0,
            )

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

    def test_stored_grok_acp_profile_can_be_restored_after_server_restart(self):
        spec = native_cli_provider_spec_from_payload(
            {
                "provider_id": "grok",
                "agent_id": "grok-low",
                "display_name": "Grok Low",
                "workspace": ".",
                "model": "grok-4.5",
                "reasoning_effort": "low",
                "permission_mode": "meeting_read_only",
            }
        )

        restored = native_cli_provider_spec_from_stored_session_strict(
            {
                "session_id": spec.agent_id,
                "participant_id": spec.agent_id,
                "display_name": spec.display_name,
                "provider_kind": spec.provider_kind,
                "workspace": spec.cwd,
                "model": spec.model,
                "reasoning_effort": spec.reasoning_effort,
                "service_tier": spec.service_tier,
                "variant": spec.variant,
                "permission_mode": spec.permission_mode,
                "runtime_kind": spec.runtime_kind,
                "transport": "pty",
                "runtime_profile_key": replace(spec, transport="pty").runtime_profile_key(),
                "command_configured": list(spec.command),
            }
        )

        self.assertEqual(restored.transport, "acp_stdio")
        self.assertEqual(restored.command, spec.command)
        self.assertEqual(restored.runtime_profile_key(), spec.runtime_profile_key())

    def test_similar_grok_pty_profile_is_not_migrated(self):
        definition = native_cli_provider_definition("grok")
        self.assertIsNotNone(definition)
        spec = definition.make_selected_spec(
            agent_id="grok-low",
            display_name="Grok Low",
            cwd="/tmp/workspace",
            model="grok-4.5",
            reasoning_effort="low",
            permission_mode="meeting_read_only",
        )

        with self.assertRaises(StoredProviderProfileError) as raised:
            native_cli_provider_spec_from_stored_session_strict(
                {
                    "participant_id": spec.agent_id,
                    "display_name": spec.display_name,
                    "provider_kind": spec.provider_kind,
                    "workspace": spec.cwd,
                    "model": spec.model,
                    "reasoning_effort": spec.reasoning_effort,
                    "service_tier": spec.service_tier,
                    "variant": spec.variant,
                    "permission_mode": spec.permission_mode,
                    "runtime_kind": spec.runtime_kind,
                    "transport": "pty",
                    "command_configured": list(spec.command),
                    "runtime_profile_key": "not-the-known-grok-profile",
                }
            )

        self.assertEqual(raised.exception.code, "provider_definition_changed")

    def test_legacy_claude_startup_marker_profile_is_migrated(self):
        definition = native_cli_provider_definition("claude")
        self.assertIsNotNone(definition)
        spec = definition.make_selected_spec(
            agent_id="claude-low",
            display_name="Claude Low",
            cwd="/tmp/workspace",
            model="claude-haiku-4-5",
            reasoning_effort="low",
            service_tier="default",
            permission_mode="meeting_read_only",
        )
        legacy_spec = replace(spec, startup_ready_contains="plan mode on")

        restored = native_cli_provider_spec_from_stored_session_strict(
            {
                "participant_id": spec.agent_id,
                "display_name": spec.display_name,
                "provider_kind": spec.provider_kind,
                "workspace": spec.cwd,
                "model": spec.model,
                "reasoning_effort": spec.reasoning_effort,
                "service_tier": spec.service_tier,
                "variant": spec.variant,
                "permission_mode": spec.permission_mode,
                "runtime_kind": spec.runtime_kind,
                "transport": spec.transport,
                "command_configured": list(spec.command),
                "runtime_profile_key": legacy_spec.runtime_profile_key(),
            }
        )

        self.assertEqual(restored.startup_ready_contains, "")
        self.assertEqual(restored.runtime_profile_key(), spec.runtime_profile_key())

    def test_unknown_claude_profile_change_still_requires_migration(self):
        definition = native_cli_provider_definition("claude")
        self.assertIsNotNone(definition)
        spec = definition.make_selected_spec(
            agent_id="claude-low",
            display_name="Claude Low",
            cwd="/tmp/workspace",
            model="claude-haiku-4-5",
            reasoning_effort="low",
            service_tier="default",
            permission_mode="meeting_read_only",
        )

        with self.assertRaises(StoredProviderProfileError) as raised:
            native_cli_provider_spec_from_stored_session_strict(
                {
                    "participant_id": spec.agent_id,
                    "display_name": spec.display_name,
                    "provider_kind": spec.provider_kind,
                    "workspace": spec.cwd,
                    "model": spec.model,
                    "reasoning_effort": spec.reasoning_effort,
                    "service_tier": spec.service_tier,
                    "variant": spec.variant,
                    "permission_mode": spec.permission_mode,
                    "runtime_kind": spec.runtime_kind,
                    "transport": spec.transport,
                    "command_configured": list(spec.command),
                    "runtime_profile_key": "unknown-claude-profile",
                }
            )

        self.assertEqual(raised.exception.code, "profile_migration_required")

    def test_public_catalog_is_safe_and_unknown_provider_is_clear(self):
        payload = native_cli_provider_catalog_payload()

        self.assertEqual(
            [provider["id"] for provider in payload],
            ["codex", "antigravity", "grok", "claude", "opencode", "deepseek"],
        )
        self.assertTrue(all(provider["interactive"] for provider in payload))
        self.assertTrue(all("command" not in provider for provider in payload))
        with self.assertRaises(UnsupportedNativeCliProvider):
            native_cli_provider_spec_from_payload({"provider_id": "unknown"})

    def test_selected_provider_spec_does_not_fill_missing_profile_values(self):
        with self.assertRaisesRegex(ValueError, "model is required"):
            native_cli_provider_spec_from_payload(
                {
                    "provider_id": "claude",
                    "display_name": "Reviewer",
                    "workspace": ".",
                    "reasoning_effort": "low",
                    "service_tier": "default",
                    "permission_mode": "meeting_read_only",
                }
            )
        with self.assertRaisesRegex(ValueError, "workspace is required"):
            native_cli_provider_spec_from_payload(
                {
                    "provider_id": "codex",
                    "display_name": "Codex",
                    "model": "gpt-5.6-luna",
                    "reasoning_effort": "low",
                    "service_tier": "default",
                    "permission_mode": "meeting_read_only",
                }
            )

    def test_stored_session_profile_is_not_filled_from_current_defaults(self):
        with self.assertRaises(StoredProviderProfileError) as incomplete:
            native_cli_provider_spec_from_stored_session_strict(
                {
                    "session_id": "codex",
                    "participant_id": "codex",
                    "display_name": "Codex",
                    "provider_kind": "codex_live_session",
                    "workspace": ".",
                    "model": "",
                    "permission_mode": "meeting_read_only",
                    "runtime_kind": "live_cli",
                    "transport": "pty",
                    "runtime_profile_key": "profile",
                    "command_configured": ["codex"],
                }
            )
        self.assertEqual(incomplete.exception.code, "profile_incomplete")


if __name__ == "__main__":
    unittest.main()
