from __future__ import annotations

import unittest

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.room.text import clean_room_text


class RoomTextTests(unittest.TestCase):
    def test_current_and_legacy_names_preserve_bounded_normalization(self) -> None:
        value = "  first\nsecond\rthird  "

        self.assertEqual(clean_room_text(value, 18), "first second third")
        self.assertEqual(
            clean_lobby_text(value, 18),
            clean_room_text(value, 18),
        )


if __name__ == "__main__":
    unittest.main()
