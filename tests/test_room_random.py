from __future__ import annotations

import unittest

from agentsassemble.providers.room_random import (
    RoomRandomError,
    choose_random,
    roll_dice,
)


class RoomRandomTests(unittest.TestCase):
    def test_roll_dice_normalizes_notation_and_reports_each_roll(self) -> None:
        values = iter([0, 5])

        result = roll_dice("2d6+3", randbelow=lambda _sides: next(values))

        self.assertEqual(
            result,
            {
                "notation": "2d6+3",
                "rolls": [1, 6],
                "modifier": 3,
                "total": 10,
            },
        )

    def test_roll_dice_rejects_unbounded_or_malformed_requests(self) -> None:
        for notation in ("", "d1", "101d6", "2d6 plus 3"):
            with self.subTest(notation=notation):
                with self.assertRaises(RoomRandomError):
                    roll_dice(notation)

    def test_choose_random_returns_the_selected_option_and_index(self) -> None:
        result = choose_random(
            ["scout", "negotiate", "retreat"],
            randbelow=lambda _count: 1,
        )

        self.assertEqual(
            result,
            {
                "choice": "negotiate",
                "index": 1,
                "option_count": 3,
            },
        )

    def test_choose_random_rejects_missing_or_empty_options(self) -> None:
        for options in (
            [],
            ["only"],
            ["north", ""],
            "north",
            [str(index) for index in range(51)],
        ):
            with self.subTest(options=options):
                with self.assertRaises(RoomRandomError):
                    choose_random(options)


if __name__ == "__main__":
    unittest.main()
