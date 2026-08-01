import json
import sqlite3
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


class GrokAndCursorSessionTests(unittest.TestCase):
    """Both keep per-folder stores; the old code reported neither."""

    def test_grok_lists_by_url_encoded_workspace_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            wanted = home / ".grok" / "sessions" / "%2Fw%2Fwanted" / "sess-1"
            wanted.mkdir(parents=True)
            (wanted / "summary.json").write_text(
                json.dumps({"info": {"id": "sess-1", "cwd": "/w/wanted"}}), encoding="utf-8"
            )
            (wanted.parent / "prompt_history.jsonl").write_text(
                json.dumps({"session_id": "sess-1", "prompt": "첫 질문", "is_bash": False}) + "\n",
                encoding="utf-8",
            )
            other = home / ".grok" / "sessions" / "%2Fw%2Fother" / "sess-2"
            other.mkdir(parents=True)
            (other / "summary.json").write_text(
                json.dumps({"info": {"id": "sess-2", "cwd": "/w/other"}}), encoding="utf-8"
            )

            scoped = list_provider_sessions(
                "grok_live_session", home=home, workspace="/w/wanted"
            )

            self.assertEqual([item["session_id"] for item in scoped], ["sess-1"])
            self.assertEqual(scoped[0]["label"], "첫 질문")

    def test_cursor_lists_by_the_cwd_in_chat_meta(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            for bucket, session, cwd, title in (
                ("b1", "chat-here", "/w/wanted", "Korean Core Debate"),
                ("b2", "chat-there", "/w/other", "Elsewhere"),
            ):
                directory = home / ".cursor" / "chats" / bucket / session
                directory.mkdir(parents=True)
                (directory / "meta.json").write_text(
                    json.dumps({"cwd": cwd, "title": title, "updatedAtMs": 1784910037350}),
                    encoding="utf-8",
                )

            scoped = list_provider_sessions(
                "cursor_live_session", home=home, workspace="/w/wanted"
            )

            self.assertEqual([item["session_id"] for item in scoped], ["chat-here"])
            self.assertEqual(scoped[0]["label"], "Korean Core Debate")


class SessionLabelTests(unittest.TestCase):
    def test_a_rollout_is_labelled_by_what_the_person_typed(self) -> None:
        # Rollouts open with project instructions and plugin lists carrying the
        # user role; labelling by the first user-role record showed those.
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            _write(
                home / ".codex" / "sessions" / "r-019ed0f7-c25b-7e32-badb-3d0143541d95.jsonl",
                [
                    {"type": "session_meta", "payload": {"cwd": "/w/wanted"}},
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": "<recommended_plugins>\nnoise"}
                            ],
                        },
                    },
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "# AGENTS.md instructions"}],
                        },
                    },
                    {
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "실제로 친 말"},
                    },
                ],
            )

            sessions = list_provider_sessions(
                "codex_live_session", home=home, workspace="/w/wanted"
            )

            self.assertEqual(sessions[0]["label"], "실제로 친 말")


class OpenCodeAndOllamaSessionTests(unittest.TestCase):
    def _opencode_db(self, home: Path, rows: list[tuple]) -> None:
        database = home / ".local" / "share" / "opencode" / "opencode.db"
        database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database)
        connection.execute(
            "CREATE TABLE session (id TEXT, directory TEXT, title TEXT,"
            " time_updated INTEGER, time_archived INTEGER)"
        )
        connection.executemany("INSERT INTO session VALUES (?,?,?,?,?)", rows)
        connection.commit()
        connection.close()

    def test_opencode_lists_sessions_for_the_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            self._opencode_db(
                home,
                [
                    ("ses_here", "/w/wanted", "여기 대화", 1785509373685, None),
                    ("ses_there", "/w/other", "다른 폴더", 1785509373000, None),
                    # A trailing slash is the same folder.
                    ("ses_slash", "/w/wanted/", "슬래시", 1785509372000, None),
                ],
            )

            scoped = list_provider_sessions(
                "opencode_server", home=home, workspace="/w/wanted"
            )

            self.assertEqual(
                [item["session_id"] for item in scoped], ["ses_here", "ses_slash"]
            )
            self.assertEqual(scoped[0]["label"], "여기 대화")

    def test_opencode_hides_archived_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            self._opencode_db(
                home,
                [
                    ("ses_live", "/w/wanted", "살아있음", 1785509373685, None),
                    ("ses_gone", "/w/wanted", "보관됨", 1785509373000, 1785509999999),
                ],
            )

            scoped = list_provider_sessions(
                "opencode_server", home=home, workspace="/w/wanted"
            )

            self.assertEqual([item["session_id"] for item in scoped], ["ses_live"])

    def test_ollama_has_no_local_conversation_store(self) -> None:
        # ollama serves models over an API; there is no session to resume.
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(
                list_provider_sessions(
                    "ollama_api", home=Path(temp_dir), workspace="/w/wanted"
                ),
                [],
            )


if __name__ == "__main__":
    unittest.main()
