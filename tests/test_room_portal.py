from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.providers.room_portal import RoomPortal


class RoomPortalResultTests(unittest.TestCase):
    def test_observation_results_excludes_prior_and_invalid_activity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="codex")
            portal.prepare()
            self._append_activity(
                portal,
                {
                    "turn_id": "old-turn",
                    "operation": "roll_dice",
                    "details": {
                        "notation": "1d20",
                        "rolls": [20],
                        "modifier": 0,
                        "total": 20,
                    },
                },
            )
            portal.begin_observation("wake-a", input_up_to_seq=7)
            self._append_activity(
                portal,
                {
                    "turn_id": "wake-a",
                    "operation": "roll_dice",
                    "details": {
                        "notation": "2d6+1",
                        "rolls": [2, 5],
                        "modifier": 1,
                        "total": 8,
                        "reason": "damage",
                        "ignored": "not forwarded",
                    },
                },
                {
                    "turn_id": "wake-a",
                    "operation": "choose_random",
                    "details": {
                        "choice": "north",
                        "index": 0,
                        "option_count": 2,
                        "options": ["north", "south"],
                        "reason": "r" * 300,
                    },
                },
                {
                    "turn_id": "another-turn",
                    "operation": "roll_dice",
                    "details": {
                        "notation": "1d20",
                        "rolls": [12],
                        "modifier": 0,
                        "total": 12,
                    },
                },
                {
                    "turn_id": "wake-a",
                    "operation": "roll_dice",
                    "details": {
                        "notation": "1d20",
                        "rolls": [12],
                        "modifier": 0,
                        "total": 99,
                    },
                },
                {
                    "turn_id": "wake-a",
                    "operation": "read",
                    "details": {"content": "private"},
                },
            )

            results = portal.observation_results("wake-a")

        self.assertEqual(
            results,
            [
                {
                    "result_id": "result-00000000000000000000000000000001",
                    "operation": "roll_dice",
                    "details": {
                        "notation": "2d6+1",
                        "rolls": [2, 5],
                        "modifier": 1,
                        "total": 8,
                        "reason": "damage",
                    },
                },
                {
                    "result_id": "result-00000000000000000000000000000002",
                    "operation": "choose_random",
                    "details": {
                        "choice": "north",
                        "index": 0,
                        "option_count": 2,
                        "options": ["north", "south"],
                        "reason": "r" * 200,
                    },
                },
            ],
        )

    def test_observation_results_caps_the_number_returned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="codex")
            portal.prepare()
            portal.begin_observation("wake-a", input_up_to_seq=7)
            self._append_activity(
                portal,
                *[
                    {
                        "turn_id": "wake-a",
                        "operation": "choose_random",
                        "details": {
                            "choice": f"choice-{index}",
                            "index": 0,
                            "option_count": 2,
                            "options": [f"choice-{index}", "other"],
                        },
                    }
                    for index in range(40)
                ],
            )

            results = portal.observation_results("wake-a")

        self.assertEqual(len(results), 32)
        self.assertEqual(results[0]["details"]["choice"], "choice-0")
        self.assertEqual(results[-1]["details"]["choice"], "choice-31")

    @staticmethod
    def _append_activity(
        portal: RoomPortal,
        *records: dict[str, object],
    ) -> None:
        with portal.activity_path.open("a", encoding="utf-8") as stream:
            for index, record in enumerate(records, start=1):
                payload = dict(record)
                if payload.get("operation") in {"roll_dice", "choose_random"}:
                    payload.setdefault("result_id", f"result-{index:032x}")
                stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    unittest.main()
