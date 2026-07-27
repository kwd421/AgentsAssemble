from __future__ import annotations

import unittest

from agentsassemble.providers.room_random import (
    RoomRandomError,
    choose_random,
    roll_dice,
)
from agentsassemble.room.system_results import validate_room_system_result


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
                "options": ["scout", "negotiate", "retreat"],
            },
        )

    def test_choose_random_rejects_missing_or_empty_options(self) -> None:
        for options in (
            [],
            ["only"],
            ["north", ""],
            ["\u200b", "\u200c"],
            "north",
            [str(index) for index in range(51)],
        ):
            with self.subTest(options=options):
                with self.assertRaises(RoomRandomError):
                    choose_random(options)

    def test_generated_results_satisfy_the_canonical_room_contract(self) -> None:
        dice = roll_dice("d20", randbelow=lambda _sides: 6)
        choice = choose_random(
            ["north\npath", "south"],
            randbelow=lambda _count: 0,
        )

        validated_dice = validate_room_system_result(
            result_id="result-11111111111111111111111111111111",
            operation="roll_dice",
            details=dice,
        )
        validated_choice = validate_room_system_result(
            result_id="result-22222222222222222222222222222222",
            operation="choose_random",
            details=choice,
        )

        self.assertEqual(validated_dice.details, dice)
        self.assertEqual(validated_choice.details, choice)


if __name__ == "__main__":
    unittest.main()
