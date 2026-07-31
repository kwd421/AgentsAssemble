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

            batch = portal.observation_result_batch("wake-a")
            results = list(batch.results)

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
        self.assertEqual(batch.malformed_count, 1)
        self.assertEqual(batch.capped_count, 0)
        self.assertFalse(batch.bytes_truncated)

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

            batch = portal.observation_result_batch("wake-a")
            results = list(batch.results)

        self.assertEqual(len(results), 32)
        self.assertEqual(results[0]["details"]["choice"], "choice-0")
        self.assertEqual(results[-1]["details"]["choice"], "choice-31")
        self.assertEqual(batch.malformed_count, 0)
        self.assertEqual(batch.capped_count, 8)
        self.assertFalse(batch.bytes_truncated)

    def test_observation_result_batch_preserves_complete_results_before_byte_cap(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="codex")
            portal.prepare()
            portal.begin_observation("wake-a", input_up_to_seq=7)
            self._append_activity(
                portal,
                {
                    "turn_id": "wake-a",
                    "operation": "roll_dice",
                    "details": {
                        "notation": "1d20",
                        "rolls": [7],
                        "modifier": 0,
                        "total": 7,
                    },
                },
            )
            with portal.activity_path.open("ab") as stream:
                stream.write(b'{"turn_id":"wake-a","operation":"roll_dice","pad":"')
                stream.write(b"x" * (70 * 1024))

            batch = portal.observation_result_batch("wake-a")

        self.assertEqual(len(batch.results), 1)
        self.assertEqual(batch.results[0]["details"]["total"], 7)
        self.assertTrue(batch.bytes_truncated)
        self.assertEqual(batch.diagnostic_count, 1)

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


class RoomPortalDeltaViewTests(unittest.TestCase):
    """The mirror must distinguish what arrived from what was already shown."""

    def _portal(self, temp_dir: str) -> RoomPortal:
        portal = RoomPortal(Path(temp_dir) / "portal", participant_id="grok")
        portal.prepare()
        return portal

    @staticmethod
    def _say(portal: RoomPortal, seq: int, name: str, text: str) -> None:
        portal.ingest_frame(
            {
                "stream": "room_events",
                "events": [
                    {
                        "type": "message_final",
                        "id": f"event-{seq}",
                        "seq": seq,
                        "participant_id": f"p-{name}",
                        "display_name": name,
                        "content": text,
                    }
                ],
            }
        )

    def test_first_turn_shows_every_message_in_full(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = self._portal(temp_dir)
            self._say(portal, 1, "호스트", "치킨 뭐 시킬까")
            self._say(portal, 2, "고죠", "양념")
            portal.begin_observation("turn-1", input_up_to_seq=2)

            view = portal.read_discussion()

            self.assertNotIn("already shown to you", view)
            self.assertIn("치킨 뭐 시킬까", view)
            self.assertIn("양념", view)

    def test_second_turn_omits_seen_messages_and_marks_the_new_one(self) -> None:
        # No recap of seen messages, even condensed: the agent's session
        # already holds them verbatim, and re-presenting settled talk as
        # current state is what caused repeated answers.
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = self._portal(temp_dir)
            self._say(portal, 1, "호스트", "치킨 뭐 시킬까")
            self._say(portal, 2, "고죠", "양념 " + "가" * 400)
            portal.begin_observation("turn-1", input_up_to_seq=2)
            portal.read_discussion()
            portal.end_observation("turn-1")

            self._say(portal, 3, "호스트", "ㅋㅋㅋㅋㅋ")
            portal.begin_observation("turn-2", input_up_to_seq=3)
            view = portal.read_discussion()

            self.assertIn("(2 earlier message(s) already shown to you", view)
            self.assertIn("New since your last read (1):", view)
            # the new line keeps its own heading and full body
            self.assertIn("## 호스트\nㅋㅋㅋㅋㅋ", view)
            # seen bodies are gone entirely, not merely shortened
            self.assertNotIn("치킨 뭐 시킬까", view)
            self.assertNotIn("가가가", view)

    def test_repeat_read_in_the_same_turn_keeps_the_same_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = self._portal(temp_dir)
            self._say(portal, 1, "호스트", "안녕")
            portal.begin_observation("turn-1", input_up_to_seq=1)
            portal.read_discussion()
            portal.end_observation("turn-1")

            self._say(portal, 2, "고죠", "왔냐")
            portal.begin_observation("turn-2", input_up_to_seq=2)
            first = portal.read_discussion()
            second = portal.read_discussion()

            self.assertEqual(first, second, "a re-read must not empty the new set")
            self.assertIn("New since your last read (1):", second)

    def test_a_turn_with_nothing_new_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = self._portal(temp_dir)
            self._say(portal, 1, "호스트", "안녕")
            portal.begin_observation("turn-1", input_up_to_seq=1)
            portal.read_discussion()
            portal.end_observation("turn-1")

            portal.begin_observation("turn-2", input_up_to_seq=1)
            view = portal.read_discussion()

            self.assertIn("New since your last read: (nothing new)", view)

    def test_the_view_shrinks_once_history_is_condensed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = self._portal(temp_dir)
            for seq in range(1, 31):
                self._say(portal, seq, "고죠", f"{seq}번 메시지 " + "가" * 300)
            portal.begin_observation("turn-1", input_up_to_seq=30)
            full = portal.read_discussion()
            portal.end_observation("turn-1")

            self._say(portal, 31, "호스트", "ㅇㅇ")
            portal.begin_observation("turn-2", input_up_to_seq=31)
            delta = portal.read_discussion()

            self.assertLess(len(delta), len(full) // 2, "the re-read must get much smaller")
