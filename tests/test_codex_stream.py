import unittest

from agentsassemble.codex_stream import parse_codex_stream, parse_codex_stream_line


class ParseCodexStreamLineTests(unittest.TestCase):
    def test_thread_started_yields_session_id(self):
        event = parse_codex_stream_line('{"type":"thread.started","thread_id":"abc-123"}')
        self.assertEqual(event, {"kind": "thread", "text": "abc-123"})

    def test_agent_message_is_a_message_chunk(self):
        event = parse_codex_stream_line('{"type":"item.completed","item":{"type":"agent_message","text":"hi"}}')
        self.assertEqual(event, {"kind": "message", "text": "hi"})

    def test_command_execution_emits_on_start_only(self):
        started = parse_codex_stream_line('{"type":"item.started","item":{"type":"command_execution","command":"ls"}}')
        self.assertEqual(started, {"kind": "command", "text": "ls"})
        completed = parse_codex_stream_line('{"type":"item.completed","item":{"type":"command_execution","command":"ls"}}')
        self.assertIsNone(completed)  # not duplicated on completion

    def test_reasoning_is_captured(self):
        event = parse_codex_stream_line('{"type":"item.completed","item":{"type":"reasoning","text":"thinking..."}}')
        self.assertEqual(event, {"kind": "reasoning", "text": "thinking..."})

    def test_noise_and_bad_lines_ignored(self):
        for line in ["", "  ", "not json", '{"type":"turn.started"}', '{"type":"turn.completed","usage":{}}', "[]"]:
            self.assertIsNone(parse_codex_stream_line(line))

    def test_full_stream_orders_events(self):
        lines = [
            '{"type":"thread.started","thread_id":"t1"}',
            '{"type":"turn.started"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"plan: read files"}}',
            '{"type":"item.started","item":{"type":"command_execution","command":"ls -la"}}',
            '{"type":"item.completed","item":{"type":"command_execution","command":"ls -la"}}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"done — 3 files"}}',
            '{"type":"turn.completed","usage":{"output_tokens":10}}',
        ]
        events = parse_codex_stream(lines)
        self.assertEqual(
            events,
            [
                {"kind": "thread", "text": "t1"},
                {"kind": "message", "text": "plan: read files"},
                {"kind": "command", "text": "ls -la"},
                {"kind": "message", "text": "done — 3 files"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
