from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

from agentsassemble.providers.cerebras import CerebrasApiRuntime
from agentsassemble.providers.room_portal import RoomPortal


def _stream(*chunks: dict[str, object]) -> io.BytesIO:
    events = "".join(
        f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        for chunk in chunks
    )
    return io.BytesIO(f"{events}data: [DONE]\n\n".encode())


class CerebrasRoomObservationTests(unittest.TestCase):
    def test_cerebras_request_completes_the_room_tool_workflow_behind_cloudflare(self):
        responses = [
            _stream(
                {
                    "model": "gpt-oss-120b",
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-read_discussion",
                                        "type": "function",
                                        "function": {
                                            "name": "read_discussion",
                                            "arguments": "{}",
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                }
            ),
            _stream(
                {
                    "model": "gpt-oss-120b",
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": index,
                                        "id": f"call-{name}",
                                        "type": "function",
                                        "function": {
                                            "name": name,
                                            "arguments": json.dumps(arguments),
                                        },
                                    }
                                    for index, (name, arguments) in enumerate(
                                        (
                                            ("roll_dice", {"notation": "1d6"}),
                                            (
                                                "choose_random",
                                                {"options": ["red", "blue"]},
                                            ),
                                            (
                                                "publish_message",
                                                {"content": "CEREBRAS_ROOM_OK"},
                                            ),
                                        )
                                    )
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                },
                {
                    "model": "gpt-oss-120b",
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "total_tokens": 120,
                    },
                },
            ),
        ]
        request_count = 0

        def cloudflare_guarded_opener(request, timeout: float):
            nonlocal request_count
            del timeout
            request_count += 1
            if request.get_header("User-agent") != "AgentsAssemble/1.0":
                raise HTTPError(request.full_url, 403, "Forbidden", {}, None)
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(
                Path(temp_dir) / "portal",
                participant_id="cerebras",
            )
            portal.prepare()
            portal.ingest_frame(
                {
                    "stream": "room_events",
                    "room_settings": {"tool_mode": "tabletop"},
                    "events": [
                        {
                            "type": "message_final",
                            "id": "host-request",
                            "seq": 5,
                            "participant_id": "host",
                            "participant_type": "human",
                            "display_name": "Host",
                            "content": "Use every assigned room tool.",
                        }
                    ],
                }
            )
            portal.begin_observation("cerebras-turn", input_up_to_seq=5)
            runtime = CerebrasApiRuntime(
                "cerebras",
                api_key="csk-private",
                opener=cloudflare_guarded_opener,
                room_portal=portal,
            )
            runtime.send_room_observation("room.wake cerebras-turn")

            activities: list[dict[str, object]] = []
            result = runtime.read_output(
                timeout_seconds=2,
                on_activity=activities.append,
            )
            receipt = portal.observation_receipt("cerebras-turn")
            results = portal.observation_results("cerebras-turn")
            publication = portal.consume_publication("cerebras-turn")

        self.assertEqual(result["content"], "RoomPortal action completed.")
        self.assertEqual(request_count, 2)
        self.assertEqual(receipt, 5)
        self.assertEqual(publication, "CEREBRAS_ROOM_OK")
        self.assertEqual(
            [
                (activity["status"], activity["activity_title"], activity.get("activity_detail"))
                for activity in activities
                if activity["activity_title"] in {"주사위 굴리기", "무작위 선택"}
            ],
            [
                ("running", "주사위 굴리기", "1d6"),
                ("completed", "주사위 굴리기", "1d6"),
                ("running", "무작위 선택", "2개 선택지"),
                ("completed", "무작위 선택", "2개 선택지"),
            ],
        )
        self.assertEqual(
            [item["operation"] for item in results],
            ["roll_dice", "choose_random"],
        )
        self.assertEqual(
            result["metadata"]["token_usage"],
            {
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
                "cache_hit_input_tokens": 0,
                "cache_miss_input_tokens": 0,
                "reasoning_tokens": 0,
            },
        )

    def test_room_tool_failure_terminates_the_visible_activity(self) -> None:
        responses = [
            _stream(
                {
                    "model": "gpt-oss-120b",
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-read",
                                        "type": "function",
                                        "function": {
                                            "name": "read_discussion",
                                            "arguments": "{}",
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                }
            ),
            _stream(
            {
                "model": "gpt-oss-120b",
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-invalid-choice",
                                    "type": "function",
                                    "function": {
                                        "name": "choose_random",
                                        "arguments": json.dumps({"options": "not-a-list"}),
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
            ),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="cerebras")
            portal.prepare()
            portal.begin_observation("cerebras-turn", input_up_to_seq=1)
            runtime = CerebrasApiRuntime(
                "cerebras",
                api_key="csk-private",
                opener=lambda *args, **kwargs: responses.pop(0),
                room_portal=portal,
            )
            runtime.send_room_observation("room.wake cerebras-turn")
            activities: list[dict[str, object]] = []

            with self.assertRaisesRegex(RuntimeError, "options must be a list"):
                runtime.read_output(
                    timeout_seconds=2,
                    on_activity=activities.append,
                )

        self.assertEqual(
            [
                activity["status"]
                for activity in activities
                if activity["activity_title"] == "무작위 선택"
            ],
            ["running", "failed"],
        )


if __name__ == "__main__":
    unittest.main()


class CerebrasEmptyRoundDiagnosticsTests(unittest.TestCase):
    """An empty round must say why, so the operator knows what to change."""

    def _run_and_capture_error(self, chunk: dict[str, object]) -> str:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = CerebrasApiRuntime(
                "cerebras",
                api_key="csk-private",
                opener=lambda *args, **kwargs: _stream(chunk),
            )
            runtime.send("hello")
            with self.assertRaises(RuntimeError) as caught:
                runtime.read_output(timeout_seconds=2)
            return str(caught.exception)

    def test_budget_exhausted_by_reasoning_is_reported_as_such(self) -> None:
        message = self._run_and_capture_error(
            {
                "model": "gpt-oss-120b",
                "choices": [
                    {
                        "delta": {"reasoning_content": "생각만 하다 예산 소진"},
                        "finish_reason": "length",
                    }
                ],
            }
        )

        self.assertIn("최대 응답 길이", message)
        self.assertIn("추론에만", message)
        self.assertNotIn("completed without a final message", message)

    def test_a_genuinely_empty_round_still_names_the_finish_reason(self) -> None:
        message = self._run_and_capture_error(
            {
                "model": "gpt-oss-120b",
                "choices": [{"delta": {}, "finish_reason": "stop"}],
            }
        )

        self.assertIn("finish_reason: stop", message)
