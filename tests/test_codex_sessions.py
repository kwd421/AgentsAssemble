import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.codex_sessions import (
    build_codex_live_agent_config,
    build_codex_live_invite_config,
    list_codex_sessions,
    write_agent_config,
)
from agentsassemble.live_agent_runner import load_group_configs


class CodexSessionTests(unittest.TestCase):
    def test_list_codex_sessions_reads_index_newest_first_and_ignores_bad_lines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index_path = Path(temp_dir) / "session_index.jsonl"
            index_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "id": "old",
                                "thread_name": "Old handoff",
                                "updated_at": "2026-05-15T00:00:00Z",
                            }
                        ),
                        "{not json",
                        json.dumps({"thread_name": "missing id", "updated_at": "2026-05-17T00:00:00Z"}),
                        json.dumps(
                            {
                                "id": "new",
                                "thread_name": "New handoff",
                                "updated_at": "2026-05-17T00:00:00Z",
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            sessions = list_codex_sessions(index_path=index_path, limit=1)

            self.assertEqual(
                sessions,
                [
                    {
                        "id": "new",
                        "thread_name": "New handoff",
                        "updated_at": "2026-05-17T00:00:00Z",
                    }
                ],
            )

    def test_build_codex_live_invite_config_preserves_existing_sections_and_upserts_role(self):
        existing = {
            "incoming_agents": [{"agent_id": "friend"}],
            "notes": "keep me",
            "providers": [{"id": "other", "kind": "mock", "display_name": "Other"}],
            "permission_profiles": [{"id": "other_perm", "meeting_read": True}],
            "agent_bindings": [
                {
                    "agent_id": "existing-lore",
                    "role_id": "lore_lawyer",
                    "owner_id": "host",
                    "provider_id": "other",
                    "permission_profile_id": "other_perm",
                    "join_mode": "fresh",
                }
            ],
        }

        config = build_codex_live_invite_config(
            session_id="019e3038-39cc-76a2-a746-5ba8c0f3b408",
            role_id="lore_lawyer",
            role_ids=["lore_lawyer", "show_me_the_feats"],
            existing=existing,
        )

        self.assertEqual(config["incoming_agents"], [{"agent_id": "friend"}])
        self.assertEqual(config["notes"], "keep me")
        self.assertIn("codex-live", [provider["id"] for provider in config["providers"]])
        self.assertIn("codex_live_meeting_readonly", [profile["id"] for profile in config["permission_profiles"]])
        bindings = {binding["role_id"]: binding for binding in config["agent_bindings"]}
        self.assertEqual(bindings["lore_lawyer"]["agent_id"], "existing-lore")
        self.assertEqual(bindings["lore_lawyer"]["provider_id"], "codex-live")
        self.assertEqual(bindings["lore_lawyer"]["join_mode"], "current_session")
        self.assertEqual(bindings["lore_lawyer"]["session_id"], "019e3038-39cc-76a2-a746-5ba8c0f3b408")
        self.assertEqual(bindings["show_me_the_feats"]["join_mode"], "fresh")

    def test_build_codex_live_invite_config_rejects_duplicate_session_id_for_other_role(self):
        existing = {
            "agent_bindings": [
                {
                    "agent_id": "codex-live-feats",
                    "role_id": "show_me_the_feats",
                    "owner_id": "host",
                    "provider_id": "codex-live",
                    "permission_profile_id": "codex_live_meeting_readonly",
                    "join_mode": "current_session",
                    "session_id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                }
            ]
        }

        with self.assertRaisesRegex(ValueError, "already invited for role show_me_the_feats"):
            build_codex_live_invite_config(
                session_id="019e3038-39cc-76a2-a746-5ba8c0f3b408",
                role_id="lore_lawyer",
                role_ids=["lore_lawyer", "show_me_the_feats"],
                existing=existing,
            )

    def test_build_codex_live_agent_config_from_invite_bindings(self):
        invite_config = {
            "agent_bindings": [
                {
                    "agent_id": "codex-live-lore",
                    "role_id": "lore_lawyer",
                    "provider_id": "codex-live",
                    "join_mode": "current_session",
                    "session_id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                },
                {
                    "agent_id": "codex-live-feats",
                    "role_id": "show_me_the_feats",
                    "provider_id": "codex-live",
                    "join_mode": "fresh",
                },
                {
                    "agent_id": "mock-agent",
                    "role_id": "mock_role",
                    "provider_id": "mock",
                },
            ]
        }

        resident_config = build_codex_live_agent_config(
            invite_config,
            server="http://room.local",
            meeting_id="resident-m1",
            engagement_mode="moderator_called",
        )

        self.assertEqual(resident_config["server"], "http://room.local")
        self.assertEqual(resident_config["poll_interval"], 2)
        self.assertEqual(resident_config["heartbeat_interval"], 30)
        self.assertEqual(resident_config["cooldown"], 5)
        self.assertEqual(resident_config["max_chain_depth"], 1)
        self.assertEqual(
            resident_config["agents"],
            [
                {
                    "agent_id": "codex-live-lore",
                    "display_name": "codex-live-lore",
                    "provider_kind": "codex_live_session",
                    "connection_kind": "live_session",
                    "session_id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                    "meeting_id": "resident-m1",
                    "engagement_mode": "moderator_called",
                    "timeout_seconds": 240,
                },
                {
                    "agent_id": "codex-live-feats",
                    "display_name": "codex-live-feats",
                    "provider_kind": "codex_live_session",
                    "connection_kind": "live_session",
                    "session_id": "",
                    "meeting_id": "resident-m1",
                    "engagement_mode": "moderator_called",
                    "timeout_seconds": 240,
                },
            ],
        )

    def test_built_codex_live_agent_config_loads_as_resident_group_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "live-agents.codex-session.local.json"
            write_agent_config(
                path,
                build_codex_live_agent_config(
                    {
                        "agent_bindings": [
                            {
                                "agent_id": "codex-live-lore",
                                "role_id": "lore_lawyer",
                                "provider_id": "codex-live",
                                "session_id": "019e3038-39cc-76a2-a746-5ba8c0f3b408",
                            }
                        ]
                    },
                    server="http://room.local",
                    meeting_id="resident-m1",
                    engagement_mode="moderator_called",
                ),
            )

            loaded = load_group_configs(path)

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].agent_id, "codex-live-lore")
            self.assertEqual(loaded[0].provider_kind, "codex_live_session")
            self.assertEqual(loaded[0].connection_kind, "live_session")
            self.assertEqual(loaded[0].command, ["codex"])
            self.assertEqual(loaded[0].session_id, "019e3038-39cc-76a2-a746-5ba8c0f3b408")
            self.assertEqual(loaded[0].meeting_id, "resident-m1")
            self.assertEqual(loaded[0].engagement_mode, "moderator_called")


if __name__ == "__main__":
    unittest.main()
