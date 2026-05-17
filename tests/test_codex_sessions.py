import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.codex_sessions import (
    build_codex_live_invite_config,
    list_codex_sessions,
)


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


if __name__ == "__main__":
    unittest.main()
