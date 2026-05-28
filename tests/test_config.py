import unittest
import json
import tempfile
from pathlib import Path

from agentsassemble.config import (
    agent_bindings_from_config,
    load_agent_runtime_config,
    load_council_config,
    permissions_from_config,
    providers_from_config,
)
from agentsassemble.live_agent_runner import load_group_configs
from agentsassemble.models import normalize_engagement_mode


class ConfigTests(unittest.TestCase):
    def test_load_demo_council_config(self):
        config = load_council_config()

        self.assertEqual(config.topic, "One Piece admiral strength debate")
        self.assertEqual(config.display_topic, "원피스 3대장 최강자 토론")
        self.assertEqual(config.display_question, "원피스 3대장 중 누가 제일 센가?")
        self.assertEqual([role.id for role in config.roles], ["lore_lawyer", "show_me_the_feats", "fanboard_skeptic"])
        self.assertEqual(config.roles[0].personality["preset"], "pedantic_lore_nerd")
        self.assertIn("dcinside", config.roles[2].source_preferences[0])
        self.assertEqual(config.meeting_mode, "debate")
        self.assertTrue(config.moderator.enabled)
        self.assertEqual([round_definition.id for round_definition in config.rounds], ["round_1", "round_2"])

    def test_load_council_config_with_meeting_mode_and_moderator(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "council.json"
            path.write_text(
                json.dumps(
                    {
                        "topic": "topic",
                        "question": "question",
                        "meeting_mode": "free_chat",
                        "moderator": {"enabled": False},
                        "roles": [
                            {
                                "id": "role_a",
                                "display_name": "A",
                                "lens": "Lens",
                                "research_focus": "focus",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            config = load_council_config(path)

        self.assertEqual(config.meeting_mode, "free_chat")
        self.assertFalse(config.moderator.enabled)
        self.assertEqual(config.moderator.to_dict(), {"enabled": False})

    def test_load_council_config_with_custom_rounds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "council.json"
            path.write_text(
                json.dumps(
                    {
                        "topic": "topic",
                        "question": "question",
                        "roles": [
                            {
                                "id": "role_a",
                                "display_name": "A",
                                "lens": "Lens",
                                "research_focus": "focus",
                            }
                        ],
                        "meeting_template": {
                            "id": "custom_template",
                            "display_name": "Custom Template",
                            "rounds": [
                                {
                                    "id": "round_1",
                                    "title": "Opening",
                                    "report_label": "Opening reports",
                                    "context_scope": "own_research",
                                    "instruction": "Open from private research.",
                                },
                                {
                                    "id": "round_2",
                                    "title": "Crossfire",
                                    "report_label": "Crossfire",
                                    "context_scope": "public_debate",
                                    "instruction": "Challenge public claims.",
                                },
                                {
                                    "id": "round_3",
                                    "title": "Final",
                                    "report_label": "Final statements",
                                    "context_scope": "public_debate",
                                    "instruction": "Give a final position.",
                                },
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = load_council_config(path)

        self.assertEqual(config.meeting_template_id, "custom_template")
        self.assertEqual(config.meeting_template_name, "Custom Template")
        self.assertEqual([round_definition.id for round_definition in config.rounds], ["round_1", "round_2", "round_3"])
        self.assertEqual(config.rounds[2].context_scope, "public_debate")

    def test_load_council_config_with_turn_control(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "council.json"
            path.write_text(
                json.dumps(
                    {
                        "topic": "topic",
                        "question": "question",
                        "roles": [
                            {
                                "id": "role_a",
                                "display_name": "A",
                                "lens": "Lens",
                                "research_focus": "focus",
                            },
                            {
                                "id": "role_b",
                                "display_name": "B",
                                "lens": "Lens",
                                "research_focus": "focus",
                            },
                        ],
                        "meeting_template": {
                            "rounds": [
                                {
                                    "id": "round_1",
                                    "title": "Selected",
                                    "context_scope": "public_debate",
                                    "instruction": "Only role A speaks.",
                                    "turn_control": {
                                        "selection": "selected_roles",
                                        "speaker_role_ids": ["role_a"],
                                        "non_speaker_mode": "watch",
                                        "moderator_instruction": "Call role A first.",
                                    },
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = load_council_config(path)

        control = config.rounds[0].turn_control
        self.assertEqual(control.selection, "selected_roles")
        self.assertEqual(control.speaker_role_ids, ["role_a"])
        self.assertEqual(control.non_speaker_mode, "watch")
        self.assertEqual(control.moderator_instruction, "Call role A first.")

    def test_turn_control_rejects_unknown_speaker_role(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "council.json"
            path.write_text(
                json.dumps(
                    {
                        "topic": "topic",
                        "question": "question",
                        "roles": [
                            {
                                "id": "role_a",
                                "display_name": "A",
                                "lens": "Lens",
                                "research_focus": "focus",
                            }
                        ],
                        "meeting_template": {
                            "rounds": [
                                {
                                    "id": "round_1",
                                    "title": "Selected",
                                    "context_scope": "public_debate",
                                    "instruction": "Unknown role speaks.",
                                    "turn_control": {
                                        "selection": "selected_roles",
                                        "speaker_role_ids": ["missing_role"],
                                    },
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Unknown turn_control speaker role"):
                load_council_config(path)

    def test_load_agent_runtime_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "agents.json"
            path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {"id": "guest-cursor", "kind": "cursor", "display_name": "Guest Cursor"}
                        ],
                        "permission_profiles": [
                            {
                                "id": "meeting_readonly_tools",
                                "official_turn": True,
                                "tool_use": True,
                                "filesystem_read": True,
                                "filesystem_write": False,
                            }
                        ],
                        "agent_bindings": [
                            {
                                "agent_id": "guest-architect",
                                "role_id": "lore_lawyer",
                                "owner_id": "friend",
                                "provider_id": "guest-cursor",
                                "permission_profile_id": "meeting_readonly_tools",
                                "join_mode": "current_session",
                                "session_id": "019e02af-c287-7cd1-aab7-c1e059c5ed44",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            data = load_agent_runtime_config(path)
            providers = providers_from_config(data)
            permissions = permissions_from_config(data)
            bindings = agent_bindings_from_config(data)

            self.assertEqual(providers["guest-cursor"].kind, "cursor")
            self.assertTrue(permissions["meeting_readonly_tools"].official_turn)
            self.assertTrue(permissions["meeting_readonly_tools"].filesystem_read)
            self.assertFalse(permissions["meeting_readonly_tools"].filesystem_write)
            self.assertEqual(bindings[0].owner_id, "friend")
            self.assertEqual(bindings[0].join_mode, "current_session")
            self.assertEqual(bindings[0].session_id, "019e02af-c287-7cd1-aab7-c1e059c5ed44")
            self.assertEqual(bindings[0].to_dict()["session_id"], "019e02af-c287-7cd1-aab7-c1e059c5ed44")
            self.assertEqual(bindings[0].engagement_mode, "moderator_called")

    def test_agent_binding_engagement_mode_can_be_configured(self):
        bindings = agent_bindings_from_config(
            {
                "agent_bindings": [
                    {
                        "agent_id": "guest-architect",
                        "role_id": "lore_lawyer",
                        "provider_id": "guest-cursor",
                        "permission_profile_id": "meeting_readonly_tools",
                        "engagement_mode": "mentioned",
                    },
                    {
                        "agent_id": "bad-mode",
                        "role_id": "fanboard_skeptic",
                        "provider_id": "guest-cursor",
                        "permission_profile_id": "meeting_readonly_tools",
                        "engagement_mode": "shout_forever",
                    },
                ]
            }
        )

        self.assertEqual(bindings[0].engagement_mode, "mentioned")
        self.assertEqual(bindings[0].to_dict()["engagement_mode"], "mentioned")
        self.assertEqual(bindings[1].engagement_mode, "manual")
        self.assertEqual(normalize_engagement_mode("watch"), "watch")
        self.assertEqual(normalize_engagement_mode("unknown"), "manual")

    def test_agent_binding_character_mode_fields_are_normalized(self):
        bindings = agent_bindings_from_config(
            {
                "agent_bindings": [
                    {
                        "agent_id": "yanagi",
                        "role_id": "lore_lawyer",
                        "provider_id": "guest-cursor",
                        "permission_profile_id": "meeting_readonly_tools",
                        "persona_card_id": "tsukishiro-yanagi",
                        "persona_path": "personas/tsukishiro-yanagi/card.json",
                        "character_mode": "work_speech_only",
                        "first_message_index": -1,
                        "persona_variables": {"mood": "dry", "nested": {"ignored": True}},
                    },
                    {
                        "agent_id": "plain",
                        "role_id": "fanboard_skeptic",
                        "provider_id": "guest-cursor",
                        "permission_profile_id": "meeting_readonly_tools",
                        "persona_card_id": "../escape",
                        "character_mode": "unknown",
                    },
                ]
            }
        )

        self.assertEqual(bindings[0].persona_card_id, "tsukishiro-yanagi")
        self.assertEqual(bindings[0].persona_card_path, "personas/tsukishiro-yanagi/card.json")
        self.assertEqual(bindings[0].character_mode, "work_speech_only")
        self.assertEqual(bindings[0].first_message_index, -1)
        self.assertEqual(bindings[0].persona_variables, {"mood": "dry"})
        self.assertEqual(bindings[0].to_dict()["persona_card_id"], "tsukishiro-yanagi")
        self.assertEqual(bindings[1].persona_card_id, "escape")
        self.assertEqual(bindings[1].character_mode, "on")

    def test_example_agent_configs_are_parseable(self):
        for path in (
            Path("configs/agents.example.json"),
            Path("configs/codex-live-session.example.json"),
            Path("configs/codex-sessions.example.json"),
            Path("configs/http-providers.example.json"),
            Path("configs/remote-bridge.example.json"),
        ):
            with self.subTest(path=path):
                data = load_agent_runtime_config(path)
                providers = providers_from_config(data)
                permissions = permissions_from_config(data)
                bindings = agent_bindings_from_config(data)

                self.assertTrue(providers)
                self.assertTrue(permissions)
                self.assertEqual(
                    [binding.role_id for binding in bindings],
                    ["lore_lawyer", "show_me_the_feats", "fanboard_skeptic"],
                )

    def test_codex_live_session_examples_share_one_three_agent_manifest(self):
        agent_data = load_agent_runtime_config(Path("configs/codex-live-session.example.json"))
        self.assertIsNotNone(agent_data)
        bindings = agent_bindings_from_config(agent_data)
        resident_configs = load_group_configs(Path("configs/live-agents.codex-session.example.json"))

        binding_ids = [binding.agent_id for binding in bindings]
        resident_ids = [config.agent_id for config in resident_configs]

        self.assertEqual(binding_ids, ["codex-live-lore", "codex-live-feats", "codex-live-skeptic"])
        self.assertEqual(resident_ids, binding_ids)
        self.assertEqual({binding.join_mode for binding in bindings}, {"fresh"})
        self.assertEqual({binding.engagement_mode for binding in bindings}, {"moderator_called"})
        self.assertEqual({binding.session_id for binding in bindings}, {None})
        self.assertEqual({config.provider_kind for config in resident_configs}, {"codex_live_session"})
        self.assertEqual({config.connection_kind for config in resident_configs}, {"live_session"})
        self.assertEqual({config.engagement_mode for config in resident_configs}, {"moderator_called"})
        self.assertEqual({config.session_id for config in resident_configs}, {""})

    def test_provider_staging_live_agent_example_covers_non_codex_cli_candidates_conservatively(self):
        resident_configs = load_group_configs(Path("configs/live-agents.provider-staging.example.json"))

        self.assertEqual(
            [config.agent_id for config in resident_configs],
            [
                "claude-code-live",
                "cursor-agent-live",
                "grok-build-live",
                "openclaw-cli-live",
            ],
        )
        self.assertEqual(
            {config.agent_id: config.provider_kind for config in resident_configs},
            {
                "claude-code-live": "claude_code",
                "cursor-agent-live": "cursor",
                "grok-build-live": "grok_build_cli",
                "openclaw-cli-live": "openclaw_cli",
            },
        )
        self.assertEqual(
            {config.agent_id: config.connection_kind for config in resident_configs},
            {
                "claude-code-live": "terminal_session",
                "cursor-agent-live": "terminal_session",
                "grok-build-live": "terminal_session",
                "openclaw-cli-live": "terminal_session",
            },
        )
        self.assertEqual({config.engagement_mode for config in resident_configs}, {"moderator_called"})


if __name__ == "__main__":
    unittest.main()
