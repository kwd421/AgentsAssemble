import json
import sys
import tempfile
import unittest
from pathlib import Path

from agentsassemble.live_cli import LiveCliRuntime, live_cli_supported
from agentsassemble.live_cli_transcripts import (
    AntigravityTranscriptMessageSource,
    CodexSessionMessageSource,
    GrokSessionMessageSource,
    LiveCliMessageExtractionError,
    LiveCliMessageSnapshot,
)


class _StaticMessageSource:
    strict = True
    fail_on_quiet_without_message = True

    def __init__(self, content: str = "") -> None:
        self.content = content
        self.started = False

    def prepare_start(self) -> None:
        return

    def begin_turn(self) -> None:
        self.started = True

    def poll(self, terminal_output: bytes, *, quiet: bool = False) -> LiveCliMessageSnapshot:
        del terminal_output, quiet
        return LiveCliMessageSnapshot(content=self.content, complete=bool(self.content), source="test")

    def describe(self) -> dict[str, object]:
        return {"message_source": "test"}


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
                                "payload": {"type": "agent_message", "message": "clean codex answer"},
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            snapshot = source.poll(b"Working... raw TUI", quiet=True)

        self.assertTrue(snapshot.complete)
        self.assertEqual(snapshot.content, "clean codex answer")

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
                        json.dumps({"type": "reasoning", "summary": [{"text": "do not show as message"}]}),
                        json.dumps({"type": "assistant", "content": "clean grok answer"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            snapshot = source.poll(b"Thinking... raw TUI", quiet=True)

        self.assertTrue(snapshot.complete)
        self.assertEqual(snapshot.content, "clean grok answer")

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
                        json.dumps({"source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "hi"}),
                        json.dumps(
                            {
                                "source": "MODEL",
                                "type": "PLANNER_RESPONSE",
                                "status": "DONE",
                                "content": "clean antigravity answer",
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
        self.assertEqual(snapshot.content, "clean antigravity answer")

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
