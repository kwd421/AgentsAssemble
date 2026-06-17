import unittest

from agentsassemble.grok_resident import parse_grok_stream_line
from agentsassemble.room_thought import ThoughtChunker


class ParseGrokStreamLineTests(unittest.TestCase):
    def test_thought_text_end(self):
        self.assertEqual(parse_grok_stream_line('{"type":"thought","data":"The"}'), {"kind": "thought", "text": "The"})
        self.assertEqual(parse_grok_stream_line('{"type":"text","data":"2"}'), {"kind": "text", "text": "2"})
        self.assertEqual(
            parse_grok_stream_line('{"type":"end","stopReason":"EndTurn","sessionId":"sid-9"}'),
            {"kind": "end", "text": "sid-9"},
        )

    def test_noise_ignored(self):
        for line in ["", "  ", "not json", '{"type":"other"}', "[]"]:
            self.assertIsNone(parse_grok_stream_line(line))


class ThoughtChunkerTests(unittest.TestCase):
    def test_flushes_on_sentence_boundary(self):
        chunker = ThoughtChunker()
        self.assertEqual(chunker.add("Hello"), [])  # no boundary yet
        self.assertEqual(chunker.add(" world."), ["Hello world."])  # period flushes
        self.assertEqual(chunker.add(" Next"), [])
        self.assertEqual(chunker.flush(), "Next")  # leftover at end

    def test_newline_is_a_boundary(self):
        chunker = ThoughtChunker()
        self.assertEqual(chunker.add("line one\n"), ["line one"])

    def test_soft_limit_prevents_unbounded_buffer(self):
        chunker = ThoughtChunker(soft_limit=40)
        out: list[str] = []
        for _ in range(10):
            out.extend(chunker.add("abcdefghij"))  # 10 chars, no enders
        # 100 chars of boundary-less text must have flushed at least once.
        self.assertTrue(out)
        self.assertTrue(all(len(c) <= 100 for c in out))


if __name__ == "__main__":
    unittest.main()
