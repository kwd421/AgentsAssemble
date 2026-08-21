from __future__ import annotations

import unittest

from agentsassemble.room.text import clean_room_text, has_room_visible_text


class RoomTextTests(unittest.TestCase):
    def test_preserves_bounded_normalization(self) -> None:
        value = "  first\nsecond\rthird  "

        self.assertEqual(clean_room_text(value, 18), "first second third")
    def test_rejects_control_only_silence(self) -> None:
        for value, expected in (
            ("", False),
            (" \n\t", False),
            ("\u200b\u2060", False),
            ("\x00", False),
            ("hello", True),
            (" \u200bhello", True),
        ):
            with self.subTest(value=repr(value)):
                self.assertEqual(has_room_visible_text(value), expected)


if __name__ == "__main__":
    unittest.main()
