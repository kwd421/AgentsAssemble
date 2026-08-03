from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.providers.deepseek import DeepSeekApiRuntime
from agentsassemble.providers.room_portal import RoomPortal


def _stream(*chunks: dict[str, object]) -> io.BytesIO:
    body = "".join(
        f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        for chunk in chunks
    )
    return io.BytesIO(f"{body}data: [DONE]\n\n".encode())


def _usage_chunk(*, input_tokens: int, output_tokens: int) -> dict[str, object]:
    return {
        "model": "deepseek-v4-flash",
        "choices": [],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "prompt_cache_hit_tokens": input_tokens // 2,
            "prompt_cache_miss_tokens": input_tokens - (input_tokens // 2),
            "completion_tokens_details": {"reasoning_tokens": output_tokens // 2},
        },
    }


class DeepSeekRoomObservationTests(unittest.TestCase):
    def test_tool_call_turn_reads_room_publishes_message_and_records_random_result(self):
        responses = [
            _stream(
                {
                    "model": "deepseek-v4-flash",
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
                                    },
                                    {
                                        "index": 1,
                                        "id": "call-publish",
                                        "type": "function",
                                        "function": {
                                            "name": "publish_message",
                                            "arguments": json.dumps(
                                                {"content": "DEEPSEEK_ROOM_OK"}
                                            ),
                                        },
                                    },
                                    {
                                        "index": 2,
                                        "id": "call-roll",
                                        "type": "function",
                                        "function": {
                                            "name": "roll_dice",
                                            "arguments": json.dumps(
                                                {"notation": "1d6"}
                                            ),
                                        },
                                    },
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                },
                _usage_chunk(input_tokens=120, output_tokens=20),
            ),
            _stream(
                {
                    "model": "deepseek-v4-flash",
                    "choices": [
                        {
                            "delta": {"content": "done"},
                            "finish_reason": "stop",
                        }
                    ],
                },
                _usage_chunk(input_tokens=180, output_tokens=10),
            ),
        ]
        request_bodies: list[dict[str, object]] = []

        def opener(request, timeout: float):
            del timeout
            request_bodies.append(json.loads(request.data))
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(
                Path(temp_dir) / "portal",
                participant_id="deepseek",
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
                            "seq": 7,
                            "participant_id": "host",
                            "participant_type": "human",
                            "display_name": "Host",
                            "content": "Use the room tools.",
                        }
                    ],
                }
            )
            portal.begin_observation("deepseek-turn", input_up_to_seq=7)
            runtime = DeepSeekApiRuntime(
                "deepseek",
                api_key="sk-private",
                opener=opener,
                room_portal=portal,
            )
            runtime.send_room_observation("room.wake deepseek-turn")

            result = runtime.read_output(timeout_seconds=2)
            receipt = portal.observation_receipt("deepseek-turn")
            random_results = portal.observation_results("deepseek-turn")
            publication = portal.consume_publication("deepseek-turn")

        self.assertEqual(result["content"], "done")
        self.assertEqual(
            result["metadata"]["token_usage"],
            {
                "input_tokens": 300,
                "output_tokens": 30,
                "total_tokens": 330,
                "cache_hit_input_tokens": 150,
                "cache_miss_input_tokens": 150,
                "reasoning_tokens": 15,
            },
        )
        self.assertEqual(len(result["metadata"]["api_calls"]), 2)
        self.assertEqual(receipt, 7)
        self.assertEqual(publication, "DEEPSEEK_ROOM_OK")
        self.assertEqual(random_results[0]["operation"], "roll_dice")
        self.assertEqual(random_results[0]["details"]["notation"], "1d6")
        self.assertTrue(request_bodies[0]["tools"])
        self.assertEqual(request_bodies[0]["tool_choice"], "auto")
        self.assertEqual(request_bodies[0]["stream_options"], {"include_usage": True})
        self.assertEqual(request_bodies[1]["messages"][-1]["role"], "tool")

    def test_long_running_tool_history_never_starts_with_an_orphan_tool_result(self):
        call_number = 0

        def opener(request, timeout: float):
            nonlocal call_number
            del timeout
            messages = json.loads(request.data)["messages"]
            for index, message in enumerate(messages):
                if message["role"] != "tool":
                    continue
                self.assertGreater(index, 0)
                previous = messages[index - 1]
                self.assertEqual(previous["role"], "assistant")
                self.assertTrue(previous.get("tool_calls"))
            call_number += 1
            if call_number % 2:
                return _stream(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": f"call-read-{call_number}",
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
                        ]
                    }
                )
            return _stream(
                {
                    "choices": [
                        {
                            "delta": {"content": "done"},
                            "finish_reason": "stop",
                        }
                    ]
                }
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="deepseek")
            portal.prepare()
            runtime = DeepSeekApiRuntime(
                "deepseek",
                api_key="sk-private",
                opener=opener,
                room_portal=portal,
            )
            for turn in range(14):
                portal.begin_observation(f"turn-{turn}", input_up_to_seq=turn)
                runtime.send_room_observation(f"room.wake turn-{turn}")
                self.assertEqual(runtime.read_output(timeout_seconds=2)["content"], "done")


if __name__ == "__main__":
    unittest.main()
