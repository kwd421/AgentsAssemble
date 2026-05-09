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


class ConfigTests(unittest.TestCase):
    def test_load_demo_council_config(self):
        config = load_council_config()

        self.assertEqual(config.topic, "One Piece admiral strength debate")
        self.assertEqual(config.display_topic, "원피스 3대장 최강자 토론")
        self.assertEqual(config.display_question, "원피스 3대장 중 누가 제일 센가?")
        self.assertEqual([role.id for role in config.roles], ["lore_lawyer", "show_me_the_feats", "fanboard_skeptic"])
        self.assertEqual(config.roles[0].personality["preset"], "pedantic_lore_nerd")
        self.assertIn("dcinside", config.roles[2].source_preferences[0])

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


if __name__ == "__main__":
    unittest.main()
