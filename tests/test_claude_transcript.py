import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.providers.claude_transcript import (
    ClaudeTranscriptTailer,
    find_claude_transcript,
    parse_claude_transcript_line,
)


class ParseClaudeTranscriptLineTests(unittest.TestCase):
    def _assistant(self, content):
        return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": content}})

    def test_text_block_is_message(self):
        line = self._assistant([{"type": "text", "text": "hello there"}])
        self.assertEqual(parse_claude_transcript_line(line), {"kind": "message", "text": "hello there"})

    def test_tool_use_includes_input_detail(self):
        line = self._assistant([{"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}}])
        self.assertEqual(parse_claude_transcript_line(line), {"kind": "command", "text": "Bash: ls -la"})
        read = self._assistant([{"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/x.py"}}])
        self.assertEqual(parse_claude_transcript_line(read), {"kind": "command", "text": "Read: /tmp/x.py"})
        bare = self._assistant([{"type": "tool_use", "name": "Foo", "input": {}}])
        self.assertEqual(parse_claude_transcript_line(bare), {"kind": "command", "text": "Foo"})

    def test_thinking_block_is_reasoning(self):
        line = self._assistant([{"type": "thinking", "thinking": "let me consider..."}])
        self.assertEqual(parse_claude_transcript_line(line), {"kind": "reasoning", "text": "let me consider..."})

    def test_string_content_is_message(self):
        line = self._assistant("just a string")
        self.assertEqual(parse_claude_transcript_line(line), {"kind": "message", "text": "just a string"})

    def test_non_assistant_and_noise_ignored(self):
        self.assertIsNone(parse_claude_transcript_line(json.dumps({"type": "user", "message": {"content": []}})))
        self.assertIsNone(parse_claude_transcript_line(json.dumps({"type": "summary"})))
        for line in ["", "  ", "not json", "[]"]:
            self.assertIsNone(parse_claude_transcript_line(line))


class FindAndTailTests(unittest.TestCase):
    def test_find_by_session_id_across_projects(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            proj = home / ".claude" / "projects" / "-Some-Cwd"
            proj.mkdir(parents=True)
            target = proj / "sid-123.jsonl"
            target.write_text("{}\n", encoding="utf-8")
            self.assertEqual(find_claude_transcript("sid-123", home=home), target)
            self.assertIsNone(find_claude_transcript("missing", home=home))

    def test_tailer_streams_only_new_lines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "t.jsonl"
            # Pre-existing history that must NOT be replayed.
            path.write_text(
                json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "old"}]}}) + "\n",
                encoding="utf-8",
            )
            tailer = ClaudeTranscriptTailer(lambda: path)
            self.assertEqual(tailer.poll(), [])  # snapshots end; old line skipped
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read"}]}}) + "\n")
                handle.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "new answer"}]}}) + "\n")
            events = tailer.poll()
            self.assertEqual(events, [{"kind": "command", "text": "Read"}, {"kind": "message", "text": "new answer"}])


if __name__ == "__main__":
    unittest.main()
