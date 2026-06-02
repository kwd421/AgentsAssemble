import tempfile
import unittest
from pathlib import Path

from agentsassemble.room_friends import (
    read_room_friends,
    room_friend_suggestions_from_agents,
    room_friend_type_for_agent,
    upsert_room_friend,
)


class RoomFriendsTests(unittest.TestCase):
    def test_upsert_room_friend_persists_type_and_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            saved = upsert_room_friend(
                root,
                {
                    "display_name": "Codex Lead",
                    "handle": "codex-lead",
                    "participant_type": "subscription_ai",
                    "provider_kind": "codex_live_session",
                    "agent_id": "codex-lead",
                },
            )
            updated = upsert_room_friend(
                root,
                {
                    "friend_id": saved["friend_id"],
                    "display_name": "Codex Director",
                    "participant_type": "subscription_ai",
                },
            )

            friends = read_room_friends(root)

        self.assertEqual(len(friends), 1)
        self.assertEqual(updated["display_name"], "Codex Director")
        self.assertTrue(friends[0]["created_at"])
        self.assertEqual(friends[0]["participant_type"], "subscription_ai")
        self.assertEqual(friends[0]["agent_id"], "codex-lead")

    def test_agent_suggestions_classify_participant_types_and_skip_saved_agents(self):
        agents = [
            {"agent_id": "codex-a", "display_name": "Codex A", "provider_kind": "codex_live_session"},
            {"agent_id": "deepseek-api", "display_name": "DeepSeek", "provider_kind": "deepseek_api"},
            {"agent_id": "llama-local", "display_name": "Llama", "provider_kind": "lmstudio_llama"},
            {"agent_id": "guest-human", "display_name": "Guest", "provider_kind": "manual"},
            {"agent_id": "remote-user", "display_name": "Remote", "connection_kind": "native_remote_room_client"},
        ]
        saved = [{"agent_id": "codex-a"}]

        suggestions = room_friend_suggestions_from_agents(agents, saved)

        self.assertEqual(
            {suggestion["agent_id"]: suggestion["participant_type"] for suggestion in suggestions},
            {
                "deepseek-api": "api",
                "llama-local": "local",
                "guest-human": "human",
                "remote-user": "remote",
            },
        )
        self.assertEqual(room_friend_type_for_agent(agents[0]), "subscription_ai")


if __name__ == "__main__":
    unittest.main()
