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
                                            ("read_discussion", {}),
                                            (
                                                "publish_message",
                                                {"content": "CEREBRAS_ROOM_OK"},
                                            ),
                                            ("roll_dice", {"notation": "1d6"}),
                                            (
                                                "choose_random",
                                                {"options": ["red", "blue"]},
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
            _stream(
                {
                    "model": "gpt-oss-120b",
                    "choices": [
                        {
                            "delta": {"content": "done"},
                            "finish_reason": "stop",
                        }
                    ],
                }
            ),
        ]

        def cloudflare_guarded_opener(request, timeout: float):
            del timeout
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

            result = runtime.read_output(timeout_seconds=2)
            receipt = portal.observation_receipt("cerebras-turn")
            results = portal.observation_results("cerebras-turn")
            publication = portal.consume_publication("cerebras-turn")

        self.assertEqual(result["content"], "done")
        self.assertEqual(receipt, 5)
        self.assertEqual(publication, "CEREBRAS_ROOM_OK")
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


if __name__ == "__main__":
    unittest.main()
