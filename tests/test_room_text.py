from __future__ import annotations

import unittest

from agentsassemble.legacy.meeting.core.events import (
    clean_lobby_text,
    has_room_visible_text as legacy_has_room_visible_text,
)
from agentsassemble.room.text import clean_room_text, has_room_visible_text


class RoomTextTests(unittest.TestCase):
    def test_current_and_legacy_names_preserve_bounded_normalization(self) -> None:
        value = "  first\nsecond\rthird  "

        self.assertEqual(clean_room_text(value, 18), "first second third")
        self.assertEqual(
            clean_lobby_text(value, 18),
            clean_room_text(value, 18),
        )

    def test_current_and_legacy_names_reject_control_only_silence(self) -> None:
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
                self.assertEqual(legacy_has_room_visible_text(value), expected)


if __name__ == "__main__":
    unittest.main()
