import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

from agentsassemble.providers.live_cli import LiveCliRuntime, live_cli_supported
from agentsassemble.providers.live_cli_transcripts import (
    AntigravityTranscriptMessageSource,
    ClaudeSessionMessageSource,
    CodexSessionMessageSource,
    CursorSessionMessageSource,
    GrokSessionMessageSource,
    LiveCliMessageExtractionError,
    LiveCliMessageSnapshot,
)


MARKDOWN_REPLY = (
    "First paragraph.\n\n"
    "Second paragraph.\n\n"
    "| Item | Status | Note |\n"
    "| --- | --- | --- |\n"
    "| Table | OK | Three columns |\n\n"
    "- first item\n"
    "- second item\n\n"
    "Inline `ok`."
)


class _StaticMessageSource:
    strict = True
    fail_on_quiet_without_message = True

    def __init__(self, content: str = "") -> None:
        self.content = content
        self.started = False

    def prepare_start(self) -> None:
        return

    def begin_turn(self, expected_input: str = "") -> None:
        del expected_input
        self.started = True

    def poll(self, terminal_output: bytes, *, quiet: bool = False) -> LiveCliMessageSnapshot:
        del terminal_output, quiet
        return LiveCliMessageSnapshot(content=self.content, complete=bool(self.content), source="test")

    def describe(self) -> dict[str, object]:
        return {"message_source": "test"}


class _WaitingStaticMessageSource(_StaticMessageSource):
    fail_on_quiet_without_message = False


class _ErrorMessageSource(_StaticMessageSource):
    def poll(self, terminal_output: bytes, *, quiet: bool = False) -> LiveCliMessageSnapshot:
        del terminal_output, quiet
        return LiveCliMessageSnapshot(error="provider completed without a reply", source="test")


def _noisy_cli_script() -> str:
    return "\n".join(
        [
            "import sys",
            "for line in sys.stdin:",
            "    if not line.strip():",
            "        continue",
            "    print('Working... Thinking - Grok Setup for #general', flush=True)",
            "    print('› Run /review on my current changes', flush=True)",
        ]
    )


class TranscriptMessageSourceTests(unittest.TestCase):
    def test_cursor_source_reads_assistant_text_without_tool_calls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "cursor_workspace"
            workspace.mkdir()
            encoded_workspace = re.sub(
                r"-+",
                "-",
                re.sub(r"[^A-Za-z0-9-]", "-", str(workspace)).strip("-"),
            )
            transcript_dir = (
                root
                / ".cursor"
                / "projects"
                / encoded_workspace
                / "agent-transcripts"
                / "session"
            )
            transcript_dir.mkdir(parents=True)
            source = CursorSessionMessageSource(home=root, cwd=workspace)
            source.prepare_start()
            source.begin_turn("hello")
            (transcript_dir / "session.jsonl").write_text(
                json.dumps(
                    {
                        "role": "user",
                        "message": {"content": [{"type": "text", "text": "hello"}]},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertFalse(source.poll(b"Cursor TUI chrome").complete)
            with (transcript_dir / "session.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "role": "assistant",
                            "message": {
                                "content": [
                                    {"type": "tool_use", "name": "Read", "input": {"path": "/tmp/private"}},
                                    {"type": "text", "text": MARKDOWN_REPLY},
                                ]
                            },
                        }
                    )
                    + "\n"
                )

            snapshot = source.poll(b"Cursor TUI chrome", quiet=True)
            source.begin_turn("second")
            (transcript_dir / "session.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "role": "user",
                                "message": {"content": [{"type": "text", "text": "hello"}]},
                            }
                        ),
                        json.dumps(
                            {
                                "role": "assistant",
                                "message": {"content": [{"type": "text", "text": MARKDOWN_REPLY}]},
                            }
                        ),
                        json.dumps(
                            {
                                "role": "user",
                                "message": {
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": "<timestamp>now</timestamp>\n<user_query>\nsecond\n</user_query>",
                                        }
                                    ]
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "role": "assistant",
                                "message": {"content": [{"type": "text", "text": "second answer"}]},
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            second = source.poll(b"Cursor TUI chrome", quiet=True)

        self.assertTrue(snapshot.complete)
        self.assertEqual(snapshot.content, MARKDOWN_REPLY)
        self.assertEqual(snapshot.source_kind, "cursor_agent_transcript_jsonl")
        self.assertEqual(second.content, "second answer")

    def test_claude_source_reads_only_assistant_text_from_current_workspace_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace_with_underscore"
            workspace.mkdir()
            encoded_workspace = re.sub(r"[^A-Za-z0-9-]", "-", str(workspace))
            project = root / ".claude" / "projects" / encoded_workspace
            project.mkdir(parents=True)
            existing = project / "existing.jsonl"
            existing.write_text(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"role": "assistant", "content": [{"type": "text", "text": "old answer"}]},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            source = ClaudeSessionMessageSource(home=root, cwd=workspace)
            source.prepare_start()
            source.begin_turn("hello")
            current = project / "current.jsonl"
            current.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "user", "message": {"role": "user", "content": "hello"}}),
                        json.dumps(
                            {
                                "type": "assistant",
                                "message": {
                                    "role": "assistant",
                                    "model": "claude-sonnet-4-6",
                                    "stop_reason": "tool_use",
                                    "content": [
                                        {"type": "thinking", "thinking": "private reasoning"},
                                        {"type": "tool_use", "name": "Read", "input": {"path": "/tmp/noise"}},
                                        {"type": "text", "text": "I will inspect that first."},
                                    ],
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            intermediate = source.poll(b"Claude TUI chrome", quiet=True)
            with current.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {
                                "role": "assistant",
                                "model": "claude-sonnet-4-6",
                                "stop_reason": "end_turn",
                                "content": [{"type": "text", "text": MARKDOWN_REPLY}],
                            },
                        }
                    )
                    + "\n"
                )
            before_completion = source.poll(b"Claude TUI chrome", quiet=True)
            with current.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"type": "system", "subtype": "turn_duration"}) + "\n")
            snapshot = source.poll(b"Claude TUI chrome", quiet=True)
            source.begin_turn("silent")
            with current.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps({"type": "user", "message": {"role": "user", "content": "silent"}})
                    + "\n"
                )
                handle.write(json.dumps({"type": "system", "subtype": "turn_duration"}) + "\n")
            silent = source.poll(b"Claude TUI chrome", quiet=True)

        self.assertFalse(intermediate.complete)
        self.assertEqual(intermediate.content, "")
        self.assertFalse(before_completion.complete)
        self.assertEqual(before_completion.content, MARKDOWN_REPLY)
        self.assertTrue(snapshot.complete)
        self.assertEqual(snapshot.content, MARKDOWN_REPLY)
        self.assertEqual(snapshot.source_kind, "claude_session_jsonl")
        self.assertEqual(snapshot.observed_model_id, "claude-sonnet-4-6")
        self.assertTrue(silent.complete)
        self.assertEqual(silent.content, "")

    def test_claude_source_reports_api_error_instead_of_exposing_it_as_room_message(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            encoded_workspace = re.sub(r"[^A-Za-z0-9-]", "-", str(workspace))
            project = root / ".claude" / "projects" / encoded_workspace
            project.mkdir(parents=True)
            source = ClaudeSessionMessageSource(home=root, cwd=workspace)
            source.prepare_start()
            source.begin_turn()
            (project / "current.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"type": "user", "message": {"role": "user", "content": "hello"}}),
                        json.dumps(
                            {
                                "type": "assistant",
                                "isApiErrorMessage": True,
                                "error": "authentication_failed",
                                "apiErrorStatus": 401,
                                "message": {
                                    "role": "assistant",
                                    "content": [{"type": "text", "text": "API Error: 401 Invalid authentication credentials"}],
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(LiveCliMessageExtractionError, "401 Invalid authentication"):
                source.poll(b"", quiet=True)

    def test_codex_source_reads_final_answer_from_session_jsonl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            session_dir = root / ".codex" / "sessions" / "2026" / "06" / "27"
            session_dir.mkdir(parents=True)
            session = session_dir / "rollout-test.jsonl"
            source = CodexSessionMessageSource(home=root, cwd=workspace)
            source.prepare_start()
            source.begin_turn()

            session.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session_meta",
                                "payload": {"cwd": str(workspace), "source": "cli"},
                            }
                        ),
                        json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "hi"}}),
                        json.dumps(
                            {
                                "type": "event_msg",
                                "payload": {"type": "agent_message", "message": MARKDOWN_REPLY},
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            snapshot = source.poll(b"Working... raw TUI", quiet=True)

        self.assertTrue(snapshot.complete)
        self.assertEqual(snapshot.content, MARKDOWN_REPLY)

    def test_codex_source_preserves_observed_model_seen_before_turn_input(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            session_dir = root / ".codex" / "sessions" / "2026" / "07" / "13"
            session_dir.mkdir(parents=True)
            session = session_dir / "rollout-model.jsonl"
            source = CodexSessionMessageSource(home=root, cwd=workspace)
            source.prepare_start()
            delivered = "answer this room turn"
            source.begin_turn(delivered)
            session.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "session_meta", "payload": {"cwd": str(workspace)}}),
                        json.dumps(
                            {
                                "type": "turn_context",
                                "payload": {"model": "gpt-5.6-luna", "cwd": str(workspace)},
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertFalse(source.poll(b"", quiet=False).complete)
            with session.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {"type": "event_msg", "payload": {"type": "user_message", "message": delivered}}
                    )
                    + "\n"
                )
                handle.write(
                    json.dumps(
                        {
                            "type": "event_msg",
                            "payload": {"type": "agent_message", "message": "model observed"},
                        }
                    )
                    + "\n"
                )
            snapshot = source.poll(b"", quiet=True)

        self.assertTrue(snapshot.complete)
        self.assertEqual(snapshot.content, "model observed")
        self.assertEqual(snapshot.observed_model_id, "gpt-5.6-luna")

    def test_codex_source_reports_task_complete_without_assistant_message(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            session_dir = root / ".codex" / "sessions" / "2026" / "07" / "10"
            session_dir.mkdir(parents=True)
            source = CodexSessionMessageSource(home=root, cwd=workspace)
            source.prepare_start()
            delivered = "shared room turn"
            source.begin_turn(delivered)
            (session_dir / "rollout-no-message.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"type": "session_meta", "payload": {"cwd": str(workspace)}}),
                        json.dumps(
                            {"type": "event_msg", "payload": {"type": "user_message", "message": delivered}}
                        ),
                        json.dumps(
                            {
                                "type": "event_msg",
                                "payload": {"type": "task_complete", "last_agent_message": None},
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            snapshot = source.poll(b"", quiet=True)

        self.assertFalse(snapshot.complete)
        self.assertEqual(snapshot.content, "")
        self.assertIn("without an assistant message", snapshot.error)

    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_runtime_raises_strict_transcript_error_without_waiting_for_timeout(self):
        runtime = LiveCliRuntime(
            "strict-error",
            [sys.executable, "-u", "-c", _noisy_cli_script()],
            idle_quiet_seconds=0.05,
            message_source=_ErrorMessageSource(),
        )
        try:
            runtime.send("hello")
            with self.assertRaisesRegex(LiveCliMessageExtractionError, "provider completed without a reply"):
                runtime.read_output(timeout_seconds=2.0)
        finally:
            runtime.stop()

    def test_codex_source_ignores_other_workspace_session_jsonl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            other_workspace = root / "other"
            workspace.mkdir()
            other_workspace.mkdir()
            session_dir = root / ".codex" / "sessions" / "2026" / "06" / "27"
            session_dir.mkdir(parents=True)
            (session_dir / "rollout-other.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"type": "session_meta", "payload": {"cwd": str(other_workspace)}}),
                        json.dumps(
                            {
                                "type": "event_msg",
                                "payload": {"type": "agent_message", "message": "wrong workspace"},
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            source = CodexSessionMessageSource(home=root, cwd=workspace)
            source.prepare_start()
            source.begin_turn()

            snapshot = source.poll(b"", quiet=True)

        self.assertFalse(snapshot.complete)
        self.assertEqual(snapshot.content, "")

    def test_codex_source_ignores_same_workspace_session_that_predates_runtime_start(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            session_dir = root / ".codex" / "sessions" / "2026" / "06" / "27"
            session_dir.mkdir(parents=True)
            old_session = session_dir / "rollout-existing.jsonl"
            old_session.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "session_meta", "payload": {"cwd": str(workspace)}}),
                        json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "hi"}}),
                        json.dumps(
                            {
                                "type": "event_msg",
                                "payload": {"type": "agent_message", "message": "wrong current session"},
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            source = CodexSessionMessageSource(home=root, cwd=workspace)
            source.prepare_start()
            source.begin_turn()
            new_session = session_dir / "rollout-new-provider.jsonl"
            new_session.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "session_meta", "payload": {"cwd": str(workspace)}}),
                        json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "hi"}}),
                        json.dumps(
                            {
                                "type": "event_msg",
                                "payload": {"type": "agent_message", "message": "clean provider session"},
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            snapshot = source.poll(b"", quiet=True)

        self.assertTrue(snapshot.complete)
        self.assertEqual(snapshot.content, "clean provider session")

    def test_codex_source_binds_to_session_containing_exact_delivered_input(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            session_dir = root / ".codex" / "sessions" / "2026" / "07" / "10"
            session_dir.mkdir(parents=True)
            source = CodexSessionMessageSource(home=root, cwd=workspace)
            source.prepare_start()
            delivered = "[Room update]\n@codex answer TARGET-1 only"
            source.begin_turn(delivered)

            target = session_dir / "rollout-target.jsonl"
            target.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "session_meta", "payload": {"cwd": str(workspace)}}),
                        json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": delivered}}),
                        json.dumps(
                            {"type": "event_msg", "payload": {"type": "agent_message", "message": "TARGET-1"}}
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            unrelated = session_dir / "rollout-newer-unrelated.jsonl"
            unrelated.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "session_meta", "payload": {"cwd": str(workspace)}}),
                        json.dumps(
                            {
                                "type": "event_msg",
                                "payload": {"type": "user_message", "message": "another Codex process input"},
                            }
                        ),
                        json.dumps(
                            {"type": "event_msg", "payload": {"type": "agent_message", "message": "WRONG"}}
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            first = source.poll(b"", quiet=True)
            source.begin_turn("target second turn")
            with unrelated.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "type": "event_msg",
                            "payload": {"type": "user_message", "message": "target second turn"},
                        }
                    )
                    + "\n"
                )
                handle.write(
                    json.dumps({"type": "event_msg", "payload": {"type": "agent_message", "message": "WRONG-2"}})
                    + "\n"
                )
            still_waiting = source.poll(b"", quiet=True)
            with target.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "type": "event_msg",
                            "payload": {"type": "user_message", "message": "target second turn"},
                        }
                    )
                    + "\n"
                )
                handle.write(
                    json.dumps({"type": "event_msg", "payload": {"type": "agent_message", "message": "TARGET-2"}})
                    + "\n"
                )
            second = source.poll(b"", quiet=True)

        self.assertEqual(first.content, "TARGET-1")
        self.assertFalse(still_waiting.complete)
        self.assertEqual(second.content, "TARGET-2")
        self.assertTrue(source.describe()["message_source_bound"])

    def test_transcript_cursor_waits_for_a_complete_jsonl_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            session_dir = root / ".codex" / "sessions" / "2026" / "07" / "10"
            session_dir.mkdir(parents=True)
            session = session_dir / "rollout-split.jsonl"
            delivered = "split record input"
            source = CodexSessionMessageSource(home=root, cwd=workspace)
            source.prepare_start()
            source.begin_turn(delivered)
            user_line = json.dumps(
                {"type": "event_msg", "payload": {"type": "user_message", "message": delivered}}
            )
            assistant_line = json.dumps(
                {"type": "event_msg", "payload": {"type": "agent_message", "message": "complete answer"}}
            )
            split_at = len(assistant_line) // 2
            session_meta = json.dumps({"type": "session_meta", "payload": {"cwd": str(workspace)}})
            session.write_text(
                session_meta + "\n" + user_line + "\n" + assistant_line[:split_at],
                encoding="utf-8",
            )

            incomplete = source.poll(b"", quiet=False)
            with session.open("a", encoding="utf-8") as handle:
                handle.write(assistant_line[split_at:] + "\n")
            complete = source.poll(b"", quiet=True)

        self.assertFalse(incomplete.complete)
        self.assertEqual(complete.content, "complete answer")

    def test_bound_antigravity_session_accepts_truncated_later_user_input(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            transcript = (
                root
                / ".gemini"
                / "antigravity-cli"
                / "brain"
                / "conversation-a"
                / ".system_generated"
                / "logs"
                / "transcript.jsonl"
            )
            transcript.parent.mkdir(parents=True)
            source = AntigravityTranscriptMessageSource(home=root, cwd=workspace)
            source.prepare_start()
            source.begin_turn("first exact input")
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "USER_INPUT",
                                "source": "USER_EXPLICIT",
                                "status": "DONE",
                                "content": "<USER_REQUEST>first exact input</USER_REQUEST>",
                            }
                        ),
                        json.dumps(
                            {
                                "type": "PLANNER_RESPONSE",
                                "source": "MODEL",
                                "status": "DONE",
                                "content": "first answer",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            first = source.poll(b"", quiet=True)

            long_input = "second exact input " + ("room context " * 1000)
            source.begin_turn(long_input)
            with transcript.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "type": "USER_INPUT",
                            "source": "USER_EXPLICIT",
                            "status": "DONE",
                            "content": (
                                "<USER_REQUEST>second exact input room context "
                                "<truncated 11900 bytes>\nlatest room line</USER_REQUEST>"
                            ),
                        }
                    )
                    + "\n"
                )
                handle.write(
                    json.dumps(
                        {
                            "type": "PLANNER_RESPONSE",
                            "source": "MODEL",
                            "status": "DONE",
                            "content": "second answer after truncated input",
                        }
                    )
                    + "\n"
                )
            second = source.poll(b"", quiet=True)

        self.assertEqual(first.content, "first answer")
        self.assertEqual(second.content, "second answer after truncated input")
        self.assertTrue(source.describe()["message_source_bound"])

    def test_antigravity_source_binds_first_turn_when_provider_truncates_long_input(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            transcript = (
                root
                / ".gemini"
                / "antigravity-cli"
                / "brain"
                / "conversation-a"
                / ".system_generated"
                / "logs"
                / "transcript.jsonl"
            )
            transcript.parent.mkdir(parents=True)
            prefix = "[Agent Session bootstrap]\n" + ("earlier room context " * 80)
            suffix = "\nlatest room message " + ("final instruction " * 40)
            delivered = prefix + ("omitted middle " * 200) + suffix
            source = AntigravityTranscriptMessageSource(home=root, cwd=workspace)
            source.prepare_start()
            source.begin_turn(delivered)
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "USER_INPUT",
                                "source": "USER_EXPLICIT",
                                "status": "DONE",
                                "content": (
                                    f"<USER_REQUEST>{prefix.rstrip()}\n"
                                    f"<truncated 3000 bytes>\n{suffix.lstrip()}</USER_REQUEST>"
                                ),
                            }
                        ),
                        json.dumps(
                            {
                                "type": "PLANNER_RESPONSE",
                                "source": "MODEL",
                                "status": "DONE",
                                "content": "answer from the first long turn",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            snapshot = source.poll(b"", quiet=True)

        self.assertEqual(snapshot.content, "answer from the first long turn")
        self.assertTrue(source.describe()["message_source_bound"])

    def test_grok_source_reads_assistant_content_from_chat_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            history = root / ".grok" / "sessions" / "workspace" / "session-a" / "chat_history.jsonl"
            history.parent.mkdir(parents=True)
            source = GrokSessionMessageSource(home=root)
            source.prepare_start()
            source.begin_turn()

            history.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "user", "content": "hello"}),
                        json.dumps({"type": "reasoning", "summary": [{"text": "do not show as message"}]}),
                        json.dumps({"type": "assistant", "content": MARKDOWN_REPLY}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            snapshot = source.poll(b"Thinking... raw TUI", quiet=True)

        self.assertTrue(snapshot.complete)
        self.assertEqual(snapshot.content, MARKDOWN_REPLY)

    def test_grok_source_matches_delivered_input_inside_structured_user_query(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            history = root / ".grok" / "sessions" / "workspace" / "session-a" / "chat_history.jsonl"
            history.parent.mkdir(parents=True)
            delivered = "[Room update]\n@grok answer TARGET only"
            source = GrokSessionMessageSource(home=root)
            source.prepare_start()
            source.begin_turn(delivered)
            history.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": f"<user_query>\n{delivered}\n</user_query>",
                                    }
                                ],
                            }
                        ),
                        json.dumps({"type": "assistant", "content": "TARGET"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            snapshot = source.poll(b"", quiet=True)

        self.assertTrue(snapshot.complete)
        self.assertEqual(snapshot.content, "TARGET")

    def test_grok_source_removes_only_trailing_provider_eos_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            history = root / ".grok" / "sessions" / "workspace" / "session-a" / "chat_history.jsonl"
            history.parent.mkdir(parents=True)
            source = GrokSessionMessageSource(home=root)
            source.prepare_start()
            source.begin_turn("reply exactly")
            history.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "user", "content": "reply exactly"}),
                        json.dumps({"type": "assistant", "content": "TARGET<|eos|>"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            snapshot = source.poll(b"", quiet=True)

        self.assertEqual(snapshot.content, "TARGET")

    def test_antigravity_source_reads_model_content_from_transcript(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = (
                root
                / ".gemini"
                / "antigravity-cli"
                / "brain"
                / "conv-a"
                / ".system_generated"
                / "logs"
                / "transcript.jsonl"
            )
            transcript.parent.mkdir(parents=True)
            source = AntigravityTranscriptMessageSource(home=root)
            source.prepare_start()
            source.begin_turn()

            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "source": "USER_EXPLICIT",
                                "type": "USER_INPUT",
                                "content": (
                                    "<USER_REQUEST>hi</USER_REQUEST>\n"
                                    "<USER_SETTINGS_CHANGE>"
                                    "The user changed setting `Model Selection` from None to "
                                    "Gemini 3.5 Flash (Medium). No need to comment on this change."
                                    "</USER_SETTINGS_CHANGE>"
                                ),
                            }
                        ),
                        json.dumps(
                            {
                                "source": "MODEL",
                                "type": "PLANNER_RESPONSE",
                                "status": "DONE",
                                "content": MARKDOWN_REPLY,
                                "thinking": "do not show as message",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            snapshot = source.poll(b"Gemini status raw TUI", quiet=True)

        self.assertTrue(snapshot.complete)
        self.assertEqual(snapshot.content, MARKDOWN_REPLY)
        self.assertEqual(snapshot.observed_model_id, "Gemini 3.5 Flash (Medium)")

    def test_antigravity_source_matches_delivered_input_inside_provider_metadata_wrapper(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = (
                root
                / ".gemini"
                / "antigravity-cli"
                / "brain"
                / "conv-a"
                / ".system_generated"
                / "logs"
                / "transcript.jsonl"
            )
            transcript.parent.mkdir(parents=True)
            delivered = "[Room update]\n@antigravity answer TARGET only"
            source = AntigravityTranscriptMessageSource(home=root)
            source.prepare_start()
            source.begin_turn(delivered)
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "source": "USER_EXPLICIT",
                                "type": "USER_INPUT",
                                "content": (
                                    f"<USER_REQUEST>\n{delivered}\n</USER_REQUEST>\n"
                                    "<ADDITIONAL_METADATA>local provider metadata</ADDITIONAL_METADATA>"
                                ),
                            }
                        ),
                        json.dumps(
                            {
                                "source": "MODEL",
                                "type": "PLANNER_RESPONSE",
                                "status": "DONE",
                                "content": "TARGET",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            snapshot = source.poll(b"", quiet=True)

        self.assertTrue(snapshot.complete)
        self.assertEqual(snapshot.content, "TARGET")

    def test_codex_source_ignores_late_previous_completion_until_next_user_input(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            session_dir = root / ".codex" / "sessions" / "2026" / "07" / "10"
            session_dir.mkdir(parents=True)
            session = session_dir / "rollout-current.jsonl"
            source = CodexSessionMessageSource(home=root, cwd=workspace)
            source.prepare_start()
            source.begin_turn()
            session.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "session_meta", "payload": {"cwd": str(workspace)}}),
                        json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "first"}}),
                        json.dumps({"type": "event_msg", "payload": {"type": "agent_message", "message": "first answer"}}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            first = source.poll(b"", quiet=True)
            source.begin_turn()
            with session.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": "first answer"}}
                    )
                    + "\n"
                )
            stale = source.poll(b"", quiet=True)
            with session.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "second"}}) + "\n")
                handle.write(json.dumps({"type": "event_msg", "payload": {"type": "agent_message", "message": "second answer"}}) + "\n")
            second = source.poll(b"", quiet=True)

        self.assertEqual(first.content, "first answer")
        self.assertFalse(stale.complete)
        self.assertEqual(second.content, "second answer")

    def test_antigravity_source_ignores_model_tool_result_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transcript = (
                root
                / ".gemini"
                / "antigravity-cli"
                / "brain"
                / "conv-a"
                / ".system_generated"
                / "logs"
                / "transcript.jsonl"
            )
            transcript.parent.mkdir(parents=True)
            source = AntigravityTranscriptMessageSource(home=root)
            source.prepare_start()
            source.begin_turn()

            transcript.write_text(
                json.dumps(
                    {
                        "source": "MODEL",
                        "type": "LIST_DIRECTORY",
                        "status": "DONE",
                        "content": "Created At: tool output, not assistant speech",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            snapshot = source.poll(b"Gemini status raw TUI", quiet=True)

        self.assertFalse(snapshot.complete)
        self.assertEqual(snapshot.content, "")


class LiveCliRuntimeExtractionTests(unittest.TestCase):
    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_live_cli_runtime_uses_transcript_message_instead_of_terminal_chrome(self):
        runtime = LiveCliRuntime(
            "codex",
            [sys.executable, "-u", "-c", _noisy_cli_script()],
            idle_quiet_seconds=0.05,
            message_source=_StaticMessageSource("clean answer"),
        )
        try:
            runtime.start()
            runtime.deliver(
                [
                    {
                        "event_id": "evt-1",
                        "actor_id": "human",
                        "actor_type": "user",
                        "kind": "user_message",
                        "content": "hello",
                    }
                ]
            )
            output = runtime.read_output(timeout_seconds=2)
        finally:
            runtime.stop()

        self.assertEqual(output["content"], "clean answer")

    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_live_cli_runtime_does_not_fallback_to_terminal_chrome_when_source_is_empty(self):
        runtime = LiveCliRuntime(
            "codex",
            [sys.executable, "-u", "-c", _noisy_cli_script()],
            idle_quiet_seconds=0.05,
            message_source=_StaticMessageSource(""),
        )
        try:
            runtime.start()
            runtime.deliver(
                [
                    {
                        "event_id": "evt-1",
                        "actor_id": "human",
                        "actor_type": "user",
                        "kind": "user_message",
                        "content": "hello",
                    }
                ]
            )
            with self.assertRaises(LiveCliMessageExtractionError):
                runtime.read_output(timeout_seconds=2)
        finally:
            runtime.stop()

    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_live_cli_runtime_reports_terminal_auth_failure_without_using_it_as_chat(self):
        script = "\n".join(
            [
                "import sys",
                "for line in sys.stdin:",
                "    if line.strip():",
                "        print('Please run /login - API Error: 401 Invalid authentication credentials', flush=True)",
            ]
        )
        runtime = LiveCliRuntime(
            "claude",
            [sys.executable, "-u", "-c", script],
            idle_quiet_seconds=0.05,
            message_source=_WaitingStaticMessageSource(""),
        )
        try:
            runtime.send("hello")
            with self.assertRaisesRegex(LiveCliMessageExtractionError, "authentication failed"):
                runtime.read_output(timeout_seconds=2)
            health = runtime.health()
        finally:
            runtime.stop()

        self.assertGreater(health["terminal_byte_count"], 0)
        self.assertEqual(
            health["terminal_tail"],
            "Provider authentication failed: run the provider's interactive login command.",
        )

    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_live_cli_runtime_reports_cursor_named_model_rejection_without_waiting_for_timeout(self):
        script = "\n".join(
            [
                "import sys",
                "for line in sys.stdin:",
                "    if line.strip():",
                "        print('Named models unavailable. Free plans can only use Auto.', flush=True)",
            ]
        )
        runtime = LiveCliRuntime(
            "cursor",
            [sys.executable, "-u", "-c", script],
            idle_quiet_seconds=0.05,
            message_source=_WaitingStaticMessageSource(""),
        )
        try:
            runtime.send("hello")
            with self.assertRaisesRegex(LiveCliMessageExtractionError, "Select Auto"):
                runtime.read_output(timeout_seconds=2)
        finally:
            runtime.stop()
