import unittest

from agentsassemble.room.system_results import (
    RoomSystemResultError,
    prepare_room_system_result,
)


class RoomSystemResultTests(unittest.TestCase):
    def test_prepares_a_bounded_dice_result_without_trusting_provider_prose(self):
        prepared = prepare_room_system_result(
            result_id="result-11111111111111111111111111111111",
            operation="roll_dice",
            details={
                "notation": "2d6+1",
                "rolls": [3, 4],
                "modifier": 1,
                "total": 8,
                "reason": "damage",
                "content": "forged system text",
            },
            participant_id="grok",
            display_name="Grok · Evandon",
            source_turn_id="turn-1",
        )

        self.assertEqual(prepared.display_name, "주사위 결과")
        self.assertEqual(
            prepared.content,
            "Grok · Evandon · 2d6+1 → 8 (굴림: 3, 4)",
        )
        self.assertEqual(
            prepared.metadata,
            {
                "room_result_id": "result-11111111111111111111111111111111",
                "room_result_kind": "dice_roll",
                "operation": "roll_dice",
                "source_turn_id": "turn-1",
                "source_participant_id": "grok",
                "details": {
                    "notation": "2d6+1",
                    "rolls": [3, 4],
                    "modifier": 1,
                    "total": 8,
                },
            },
        )
        self.assertNotIn("forged", str(prepared))

    def test_rejects_inconsistent_or_out_of_range_dice_results(self):
        invalid_details = (
            {
                "notation": "1d20",
                "rolls": [21],
                "modifier": 0,
                "total": 21,
            },
            {
                "notation": "2d6",
                "rolls": [3],
                "modifier": 0,
                "total": 3,
            },
            {
                "notation": "1d20",
                "rolls": [7],
                "modifier": 0,
                "total": 8,
            },
            {
                "notation": "1d20",
                "rolls": [True],
                "modifier": 0,
                "total": 1,
            },
        )

        for details in invalid_details:
            with self.subTest(details=details), self.assertRaises(RoomSystemResultError):
                prepare_room_system_result(
                    result_id="result-22222222222222222222222222222222",
                    operation="roll_dice",
                    details=details,
                    participant_id="grok",
                    display_name="Grok",
                    source_turn_id="turn-1",
                )

    def test_prepares_and_bounds_an_official_random_choice(self):
        prepared = prepare_room_system_result(
            result_id="result-33333333333333333333333333333333",
            operation="choose_random",
            details={
                "choice": "북쪽 길",
                "index": 1,
                "option_count": 3,
                "options": ["남쪽 길", "북쪽 길", "동쪽 길"],
                "reason": "route",
            },
            participant_id="luna",
            display_name="Luna · Lyra",
            source_turn_id="turn-2",
        )

        self.assertEqual(prepared.display_name, "무작위 선택 결과")
        self.assertEqual(
            prepared.content,
            "Luna · Lyra · 무작위 선택 → 「북쪽 길」 (2/3)",
        )
        with self.assertRaises(RoomSystemResultError):
            prepare_room_system_result(
                result_id="result-44444444444444444444444444444444",
                operation="choose_random",
                details={
                    "choice": "북쪽 길",
                    "index": 3,
                    "option_count": 3,
                    "options": ["남쪽 길", "북쪽 길", "동쪽 길"],
                },
                participant_id="luna",
                display_name="Luna · Lyra",
                source_turn_id="turn-2",
            )

        with self.assertRaises(RoomSystemResultError):
            prepare_room_system_result(
                result_id="result-55555555555555555555555555555555",
                operation="choose_random",
                details={
                    "choice": "북쪽 길",
                    "index": 0,
                    "option_count": 2,
                    "options": ["남쪽 길", "북쪽 길"],
                },
                participant_id="luna",
                display_name="Luna · Lyra",
                source_turn_id="turn-2",
            )

        with self.assertRaises(RoomSystemResultError):
            prepare_room_system_result(
                result_id="result-66666666666666666666666666666666",
                operation="choose_random",
                details={
                    "choice": "\u200b",
                    "index": 0,
                    "option_count": 2,
                    "options": ["\u200b", "\u200c"],
                },
                participant_id="luna",
                display_name="Luna · Lyra",
                source_turn_id="turn-2",
            )

    def test_rejects_a_result_without_a_tool_generated_id(self):
        with self.assertRaisesRegex(RoomSystemResultError, "id is invalid"):
            prepare_room_system_result(
                result_id="provider-made-up",
                operation="roll_dice",
                details={
                    "notation": "1d20",
                    "rolls": [7],
                    "modifier": 0,
                    "total": 7,
                },
                participant_id="grok",
                display_name="Grok",
                source_turn_id="turn-1",
            )


if __name__ == "__main__":
    unittest.main()
