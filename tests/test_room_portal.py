from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agentsassemble.providers.room_portal import RoomPortal, helper_interpreter


class RoomPortalVoteProjectionTests(unittest.TestCase):
    def test_provider_files_and_summary_expose_only_anonymous_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="codex")
            portal.prepare()
            portal.ingest_frame(
                {
                    "stream": "room_events",
                    "events": [
                        {
                            "id": "vote-1",
                            "seq": 1,
                            "type": "message_final",
                            "participant_id": "host",
                            "display_name": "Host",
                            "message_kind": "vote",
                            "vote_id": "vote-1",
                            "vote_question": "Which route?",
                            "vote_options": ["North", "South"],
                            "vote_duration_seconds": 300,
                            "vote_deadline_at": "2026-07-27T15:00:00+00:00",
                        },
                        {
                            "id": "ballot-1",
                            "seq": 2,
                            "type": "message_final",
                            "participant_id": "peer",
                            "display_name": "Peer",
                            "message_kind": "vote_cast",
                            "vote_id": "vote-1",
                            "vote_choice": "South",
                        },
                        {
                            "id": "ballot-2",
                            "seq": 3,
                            "type": "message_final",
                            "participant_id": "codex",
                            "display_name": "Codex",
                            "message_kind": "vote_cast",
                            "vote_id": "vote-1",
                            "vote_choice": "North",
                        },
                    ],
                }
            )

            view = portal.acp_read_text("/agentsassemble-room/current.md")
            message_index = json.loads(
                (portal.root / "messages.json").read_text(encoding="utf-8")
            )
            summary = portal.vote_summary("vote-1")
            portal.begin_observation("vote-snapshot", input_up_to_seq=3)
            portal.ingest_frame(
                {
                    "stream": "room_events",
                    "events": [
                        {
                            "id": "ballot-3",
                            "seq": 4,
                            "type": "message_final",
                            "participant_id": "peer",
                            "display_name": "Peer",
                            "message_kind": "vote_cast",
                            "vote_id": "vote-1",
                            "vote_choice": "North",
                        },
                        {
                            "id": "ballot-1",
                            "seq": 2,
                            "type": "message_final",
                            "participant_id": "peer",
                            "display_name": "Peer",
                            "message_kind": "vote_cast",
                            "vote_id": "vote-1",
                            "vote_choice": "South",
                        },
                    ],
                }
            )
            frozen_view = portal.read_discussion()
            frozen_summary = portal.vote_summary("vote-1")
            frozen_index = json.loads(
                (portal.root / "messages.json").read_text(encoding="utf-8")
            )
            frozen_terminal_summary = json.loads(
                subprocess.run(
                    [str(portal.helper_path), "vote-summary", "vote-1"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )
            portal.end_observation("vote-snapshot")
            changed_summary = portal.vote_summary("vote-1")
            changed_view = portal.read_discussion()

        self.assertIn("[Vote vote-1]", view)
        self.assertIn("Which route?", view)
        self.assertIn("Closes at: 2026-07-27T15:00:00+00:00", view)
        self.assertIn("1. North", view)
        self.assertIn("2. South", view)
        self.assertIn("Anonymous result: North 1, South 1", view)
        self.assertNotIn("ballot", view.casefold())
        self.assertNotIn("Peer", view)
        self.assertNotIn("Codex", view)
        self.assertTrue(
            all(
                message.get("message_kind") != "vote_cast"
                for message in message_index["messages"]
            )
        )
        self.assertEqual(summary["tallies"], {"North": 1, "South": 1})
        self.assertEqual(summary["own_choice"], "North")
        self.assertNotIn("voters", summary)
        self.assertIn("Anonymous result: North 1, South 1", frozen_view)
        self.assertEqual(frozen_summary["tallies"], {"North": 1, "South": 1})
        self.assertEqual(
            frozen_index["messages"][0]["vote_tallies"],
            {"North": 1, "South": 1},
        )
        self.assertEqual(
            frozen_terminal_summary["tallies"],
            {"North": 1, "South": 1},
        )
        self.assertEqual(changed_summary["tallies"], {"North": 2, "South": 0})
        self.assertEqual(changed_summary["own_choice"], "North")
        self.assertIn("Anonymous result: North 2, South 0", changed_view)


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
            self.assertEqual(portal.observation_receipt("turn-1"), 2)
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

    def test_unread_observation_is_shown_again_after_the_turn_ends(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = self._portal(temp_dir)
            self._say(portal, 1, "호스트", "아직 읽지 않은 메시지")
            portal.begin_observation("turn-1", input_up_to_seq=1)

            self.assertIsNone(portal.observation_receipt("turn-1"))
            portal.end_observation("turn-1")
            portal.begin_observation("turn-2", input_up_to_seq=1)
            view = portal.read_discussion()

            self.assertIn("## 호스트\n아직 읽지 않은 메시지", view)
            self.assertNotIn("already shown to you", view)

    def test_repeat_read_in_the_same_turn_keeps_the_same_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = self._portal(temp_dir)
            self._say(portal, 1, "호스트", "안녕")
            portal.begin_observation("turn-1", input_up_to_seq=1)
            portal.read_discussion()
            self.assertEqual(portal.observation_receipt("turn-1"), 1)
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
            self.assertEqual(portal.observation_receipt("turn-1"), 1)
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
            self.assertEqual(portal.observation_receipt("turn-1"), 30)
            portal.end_observation("turn-1")

            self._say(portal, 31, "호스트", "ㅇㅇ")
            portal.begin_observation("turn-2", input_up_to_seq=31)
            delta = portal.read_discussion()

            self.assertLess(len(delta), len(full) // 2, "the re-read must get much smaller")


class RoomPortalHelperInterpreterTests(unittest.TestCase):
    """The helper must not depend on whatever python3 the child's PATH finds."""

    def test_helper_shebang_is_an_absolute_interpreter(self) -> None:
        # `#!/usr/bin/env python3` resolved to a relocatable build inside agy's
        # sandbox, which then could not open its own libpython and died before
        # reading the room.
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="grok")
            portal.prepare()

            shebang = portal.helper_path.read_text(encoding="utf-8").splitlines()[0]

            self.assertTrue(shebang.startswith("#!/"), shebang)
            self.assertNotIn("/usr/bin/env", shebang)

    def test_the_written_helper_actually_executes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="grok")
            portal.prepare()

            completed = subprocess.run(
                [str(portal.helper_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_the_interpreter_carries_no_private_libpython(self) -> None:
        # The failure mode was a dylib outside the sandbox, so the chosen
        # interpreter must be one that does not need to load one.
        interpreter = helper_interpreter()
        if sys.platform != "darwin" or not Path(interpreter).is_absolute():
            self.skipTest("otool inspection is macOS-only")
        completed = subprocess.run(
            ["otool", "-L", interpreter], capture_output=True, text=True, timeout=30
        )
        self.assertNotIn("libpython", completed.stdout)

if __name__ == "__main__":
    unittest.main()
