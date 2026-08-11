from __future__ import annotations

import io
import json
import tempfile
import time
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


class _SlowKeepaliveResponse:
    def __init__(self) -> None:
        self._remaining = 3

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        if self._remaining <= 0:
            raise StopIteration
        self._remaining -= 1
        time.sleep(0.4)
        return b": keepalive\n\n"

    def close(self) -> None:
        return None


class DeepSeekRoomObservationTests(unittest.TestCase):
    def test_keepalive_stream_cannot_outlive_the_turn_deadline(self):
        runtime = DeepSeekApiRuntime(
            "deepseek",
            api_key="sk-private",
            opener=lambda _request, timeout: _SlowKeepaliveResponse(),
        )
        runtime.send("hello")

        with self.assertRaisesRegex(TimeoutError, "timed out after 1 seconds"):
            runtime.read_output(timeout_seconds=1)

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
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                },
                _usage_chunk(input_tokens=50, output_tokens=6),
            ),
            _stream(
                {
                    "model": "deepseek-v4-flash",
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-roll",
                                        "type": "function",
                                        "function": {
                                            "name": "roll_dice",
                                            "arguments": json.dumps(
                                                {"notation": "1d6"}
                                            ),
                                        },
                                    },
                                    {
                                        "index": 1,
                                        "id": "call-publish",
                                        "type": "function",
                                        "function": {
                                            "name": "publish_message",
                                            "arguments": json.dumps(
                                                {
                                                    "content": "DEEPSEEK_ROOM_OK",
                                                    "next_agent_id": "host",
                                                }
                                            ),
                                        },
                                    },
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                },
                _usage_chunk(input_tokens=70, output_tokens=14),
            ),
        ]
        request_bodies: list[dict[str, object]] = []
        activities: list[dict[str, object]] = []

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

            result = runtime.read_output(
                timeout_seconds=2,
                on_activity=activities.append,
            )
            receipt = portal.observation_receipt("deepseek-turn")
            random_results = portal.observation_results("deepseek-turn")
            publication = portal.consume_publication_result("deepseek-turn")

        self.assertEqual(result["content"], "RoomPortal action completed.")
        self.assertEqual(
            result["metadata"]["token_usage"],
            {
                "input_tokens": 120,
                "output_tokens": 20,
                "total_tokens": 140,
                "cache_hit_input_tokens": 60,
                "cache_miss_input_tokens": 60,
                "reasoning_tokens": 10,
            },
        )
        self.assertEqual(len(result["metadata"]["api_calls"]), 2)
        self.assertEqual(receipt, 7)
        self.assertEqual(publication.content, "DEEPSEEK_ROOM_OK")
        self.assertEqual(publication.target_agent_id, "")
        self.assertNotIn(
            "@host에게 전달",
            {str(activity.get("activity_detail") or "") for activity in activities},
        )
        self.assertEqual(random_results[0]["operation"], "roll_dice")
        self.assertEqual(random_results[0]["details"]["notation"], "1d6")
        self.assertTrue(request_bodies[0]["tools"])
        self.assertNotIn("tool_choice", request_bodies[0])
        self.assertNotIn("tool_choice", request_bodies[1])
        self.assertEqual(request_bodies[0]["stream_options"], {"include_usage": True})
        self.assertEqual(len(request_bodies), 2)

    def test_chat_turn_can_inspect_participants_and_stage_a_structured_vote(self):
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
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                }
            ),
            _stream(
                {
                    "model": "deepseek-v4-flash",
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-participants",
                                        "type": "function",
                                        "function": {
                                            "name": "list_participants",
                                            "arguments": "{}",
                                        },
                                    },
                                    {
                                        "index": 1,
                                        "id": "call-vote",
                                        "type": "function",
                                        "function": {
                                            "name": "create_vote",
                                            "arguments": json.dumps(
                                                {
                                                    "question": "Which patch?",
                                                    "options": ["Small", "Large"],
                                                    "duration_seconds": 300,
                                                }
                                            ),
                                        },
                                    },
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                }
            ),
        ]

        def opener(_request, timeout: float):
            del timeout
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="deepseek")
            portal.prepare()
            portal.ingest_frame(
                {
                    "room_settings": {"tool_mode": "chat"},
                    "participants": [
                        {
                            "participant_id": "host-id",
                            "participant_type": "human",
                            "display_name": "Host",
                            "role": "host",
                        },
                        {
                            "participant_id": "deepseek",
                            "participant_type": "agent",
                            "display_name": "DeepSeek",
                            "role": "agent",
                        },
                    ],
                }
            )
            portal.begin_observation("vote-turn", input_up_to_seq=4)
            runtime = DeepSeekApiRuntime(
                "deepseek",
                api_key="sk-private",
                opener=opener,
                room_portal=portal,
            )
            runtime.send_room_observation("room.wake vote-turn")
            runtime.read_output(timeout_seconds=2)
            publication = portal.consume_publication_result("vote-turn")

        self.assertEqual(publication.message_kind, "vote")
        self.assertEqual(publication.vote_question, "Which patch?")
        self.assertEqual(publication.vote_options, ("Small", "Large"))
        self.assertEqual(publication.vote_duration_seconds, 300)

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
