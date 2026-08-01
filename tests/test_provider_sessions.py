import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.providers.sessions import list_provider_sessions


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


class ProviderSessionWorkspaceScopeTests(unittest.TestCase):
    """A folder question must be answered with that folder's sessions only."""

    def test_codex_filters_by_the_cwd_recorded_in_the_rollout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            base = home / ".codex" / "sessions" / "2026" / "06" / "17"
            _write(
                base / "rollout-a-019ed0f7-c25b-7e32-badb-3d0143541d95.jsonl",
                [
                    {"type": "session_meta", "payload": {"cwd": "/w/wanted"}},
                    {"type": "user", "message": {"role": "user", "content": "here"}},
                ],
            )
            _write(
                base / "rollout-b-019ed0f7-c25b-7e32-badb-3d0143541d96.jsonl",
                [
                    {"type": "session_meta", "payload": {"cwd": "/w/other"}},
                    {"type": "user", "message": {"role": "user", "content": "elsewhere"}},
                ],
            )

            scoped = list_provider_sessions(
                "codex_live_session", home=home, workspace="/w/wanted"
            )

            self.assertEqual([item["label"] for item in scoped], ["here"])

    def test_antigravity_filters_by_the_cwd_conversation_map(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            conv = home / ".gemini" / "antigravity-cli" / "conversations"
            conv.mkdir(parents=True)
            (conv / "conv-here.db").write_bytes(b"\x00")
            (conv / "conv-there.db").write_bytes(b"\x00")
            cache = home / ".gemini" / "antigravity-cli" / "cache" / "last_conversations.json"
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(
                json.dumps({"/w/wanted": "conv-here", "/w/other": "conv-there"}),
                encoding="utf-8",
            )

            scoped = list_provider_sessions(
                "antigravity_live_session", home=home, workspace="/w/wanted"
            )

            self.assertEqual([item["session_id"] for item in scoped], ["conv-here"])

    def test_claude_returns_nothing_rather_than_every_project(self) -> None:
        # The old fallback answered "sessions in this folder" with the whole
        # store whenever the folder had no project directory yet.
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            _write(
                home / ".claude" / "projects" / "-w-other" / "s.jsonl",
                [{"type": "user", "message": {"role": "user", "content": "elsewhere"}}],
            )

            scoped = list_provider_sessions(
                "claude_code", home=home, workspace="/w/never-used"
            )

            self.assertEqual(scoped, [])


if __name__ == "__main__":
    unittest.main()
