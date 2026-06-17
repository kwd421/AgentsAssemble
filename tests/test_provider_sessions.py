import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.provider_sessions import list_provider_sessions


def _write(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


class ProviderSessionsTests(unittest.TestCase):
    def test_codex_lists_by_filename_uuid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            uuid = "019ed0f7-c25b-7e32-badb-3d0143541d95"
            _write(
                home / ".codex" / "sessions" / "2026" / "06" / "17" / f"rollout-2026-06-17T00-05-58-{uuid}.jsonl",
                [{"type": "user", "message": {"role": "user", "content": "hello codex"}}],
            )
            sessions = list_provider_sessions("codex_live_session", home=home)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["session_id"], uuid)
            self.assertEqual(sessions[0]["label"], "hello codex")

    def test_claude_scoped_to_workspace_and_labels_first_user_msg(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            proj = home / ".claude" / "projects" / "-Users-me-Proj"
            _write(
                proj / "abc-session.jsonl",
                [
                    {"type": "summary"},
                    {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "fix the bug"}]}},
                ],
            )
            sessions = list_provider_sessions("claude_code", workspace="/Users/me/Proj", home=home)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["session_id"], "abc-session")
            self.assertEqual(sessions[0]["label"], "fix the bug")

    def test_antigravity_uses_cwd_labels(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            conv = home / ".gemini" / "antigravity-cli" / "conversations"
            conv.mkdir(parents=True)
            (conv / "conv-1.db").write_bytes(b"\x00")
            cache = home / ".gemini" / "antigravity-cli" / "cache" / "last_conversations.json"
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"/Users/me/MyRepo": "conv-1"}), encoding="utf-8")
            sessions = list_provider_sessions("antigravity_live_session", home=home)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["session_id"], "conv-1")
            self.assertEqual(sessions[0]["label"], "MyRepo")

    def test_grok_returns_empty_and_missing_dirs_are_safe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            self.assertEqual(list_provider_sessions("grok_live_session", home=home), [])
            self.assertEqual(list_provider_sessions("codex_live_session", home=home), [])
            self.assertEqual(list_provider_sessions("unknown_kind", home=home), [])


if __name__ == "__main__":
    unittest.main()
