import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.features.social.friends import (
    delete_room_friend,
    read_room_friends,
    room_friends_payload,
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

    def test_delete_room_friend_removes_saved_entry_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            upsert_room_friend(
                root,
                {
                    "friend_id": "friend:codex-lead",
                    "display_name": "Codex Lead",
                    "participant_type": "subscription_ai",
                },
            )
            upsert_room_friend(
                root,
                {
                    "friend_id": "friend:seinel",
                    "display_name": "SeiNel",
                    "participant_type": "human",
                },
            )

            deleted = delete_room_friend(root, "friend:codex-lead")
            friends = read_room_friends(root)

        self.assertEqual(deleted["friend_id"], "friend:codex-lead")
        self.assertEqual([friend["friend_id"] for friend in friends], ["friend:seinel"])

    def test_delete_room_friend_rejects_missing_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            with self.assertRaises(ValueError):
                delete_room_friend(root, "")
            with self.assertRaises(ValueError):
                delete_room_friend(root, "friend:missing")

    def test_agent_suggestions_classify_participant_types_and_skip_saved_agents(self):
        agents = [
            {"agent_id": "codex-a", "display_name": "Codex A", "provider_kind": "codex_live_session"},
            {"agent_id": "deepseek-api", "display_name": "DeepSeek", "provider_kind": "deepseek_api"},
            {"agent_id": "llama-local", "display_name": "Llama", "provider_kind": "lmstudio_llama"},
            {"agent_id": "guest-human", "display_name": "Guest", "provider_kind": "manual"},
            {
                "agent_id": "gpt-54-mini-smoke",
                "display_name": "GPT-5.4 Mini",
                "provider_kind": "codex",
                "connection_kind": "manual",
            },
            {
                "agent_id": "grok-build",
                "display_name": "Grok Build",
                "provider_kind": "grok_build_cli",
                "connection_kind": "local_cli",
            },
            {
                "agent_id": "yanagi-local",
                "display_name": "츠키시로 야나기",
                "provider_kind": "local_cli",
                "connection_kind": "local_cli",
            },
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
                "gpt-54-mini-smoke": "subscription_ai",
                "grok-build": "subscription_ai",
                "yanagi-local": "local",
                "remote-user": "remote",
            },
        )
        self.assertEqual(room_friend_type_for_agent(agents[0]), "subscription_ai")

    def test_read_room_friends_repairs_stale_human_ai_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "room_friends.json").write_text(
                json.dumps(
                    {
                        "friends": [
                            {
                                "friend_id": "agent:gpt-54-mini-smoke",
                                "display_name": "GPT-5.4 Mini",
                                "handle": "gpt-54-mini-smoke",
                                "participant_type": "human",
                                "provider_kind": "codex",
                                "connection_kind": "manual",
                            },
                            {
                                "friend_id": "friend:seinel",
                                "display_name": "SeiNel",
                                "handle": "seinel",
                                "participant_type": "human",
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            friends = read_room_friends(root)

        by_id = {str(friend["friend_id"]): friend for friend in friends}
        self.assertEqual(by_id["agent:gpt-54-mini-smoke"]["participant_type"], "subscription_ai")
        self.assertEqual(by_id["friend:seinel"]["participant_type"], "human")

    def test_read_room_friends_does_not_mark_agentless_ai_records_online(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "room_friends.json").write_text(
                json.dumps(
                    {
                        "friends": [
                            {
                                "friend_id": "friend:dm-draft-beta",
                                "display_name": "DM Draft Beta",
                                "participant_type": "subscription_ai",
                                "provider_kind": "claude",
                                "connection_kind": "claude_code",
                                "status": "online",
                                "source": "browser-check",
                            },
                            {
                                "friend_id": "friend:seinel",
                                "display_name": "SeiNel",
                                "participant_type": "human",
                                "status": "online",
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            friends = read_room_friends(root)

        by_id = {str(friend["friend_id"]): friend for friend in friends}
        self.assertEqual(by_id["friend:dm-draft-beta"]["status"], "pending")
        self.assertEqual(by_id["friend:seinel"]["status"], "online")

    def test_room_friends_payload_uses_live_heartbeat_overlay_for_saved_ai_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "room_friends.json").write_text(
                json.dumps(
                    {
                        "friends": [
                            {
                                "friend_id": "friend:codex-dm",
                                "display_name": "Codex DM",
                                "participant_type": "subscription_ai",
                                "provider_kind": "codex",
                                "connection_kind": "live_session",
                                "agent_id": "codex-dm",
                                "source_agent_id": "codex-dm",
                                "status": "online",
                                "last_seen_at": "2026-06-01T00:00:00+00:00",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            offline_payload = room_friends_payload(root, [])
            online_payload = room_friends_payload(
                root,
                [
                    {
                        "agent_id": "codex-dm",
                        "display_name": "Codex DM Live",
                        "provider_kind": "codex",
                        "connection_kind": "live_session",
                        "meeting_id": "resident-m1",
                        "status": "online",
                        "last_seen_at": "2026-06-04T00:00:00+00:00",
                    }
                ],
            )

        offline_friend = offline_payload["friends"][0]
        online_friend = online_payload["friends"][0]
        self.assertEqual(offline_friend["status"], "pending")
        self.assertEqual(online_friend["status"], "online")
        self.assertEqual(online_friend["last_seen_at"], "2026-06-04T00:00:00+00:00")
        self.assertEqual(online_friend["last_meeting_id"], "resident-m1")

if __name__ == "__main__":
    unittest.main()
