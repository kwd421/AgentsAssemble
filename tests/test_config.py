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
        self.assertEqual([round_definition.id for round_definition in config.rounds], ["round_1", "round_2"])

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

    def test_example_agent_configs_are_parseable(self):
        for path in (
            Path("configs/agents.example.json"),
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


if __name__ == "__main__":
    unittest.main()
