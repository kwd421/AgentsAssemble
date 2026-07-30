from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.providers.local_openai import LocalOpenAICompatibleRuntime
from agentsassemble.providers.room_portal import RoomPortal


def _stream(*chunks: dict[str, object]) -> io.BytesIO:
    body = "".join(
        f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        for chunk in chunks
    )
    return io.BytesIO(f"{body}data: [DONE]\n\n".encode())


class LocalOpenAIRoomObservationTests(unittest.TestCase):
    def test_published_observation_does_not_require_a_redundant_private_final(self) -> None:
        responses = [
            _stream(
                {
                    "model": "gemma-4-e4b-it",
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
                                                {"content": "LOCAL_ROOM_OK"}
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
            _stream(
                {
                    "model": "gemma-4-e4b-it",
                    "choices": [
                        {
                            "delta": {},
                            "finish_reason": "stop",
                        }
                    ],
                }
            ),
        ]

        def opener(_request, timeout: float):
            del timeout
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(
                Path(temp_dir) / "portal",
                participant_id="lmstudio",
            )
            portal.prepare()
            portal.ingest_frame(
                {
                    "stream": "room_events",
                    "events": [
                        {
                            "type": "message_final",
                            "id": "host-request",
                            "seq": 4,
                            "participant_id": "host",
                            "participant_type": "human",
                            "display_name": "Host",
                            "content": "Read and answer in the room.",
                        }
                    ],
                }
            )
            portal.begin_observation("local-turn", input_up_to_seq=4)
            runtime = LocalOpenAICompatibleRuntime(
                "lmstudio",
                provider_name="LM Studio",
                model="gemma-4-e4b-it",
                base_url="http://127.0.0.1:1234/v1",
                message_source="lmstudio_sse",
                opener=opener,
                room_portal=portal,
            )
            runtime.send_room_observation("room.wake local-turn")

            result = runtime.read_output(timeout_seconds=2)
            receipt = portal.observation_receipt("local-turn")
            publication = portal.consume_publication("local-turn")

        self.assertEqual(result["outcome"], "message")
        self.assertTrue(result["content"])
        self.assertEqual(receipt, 4)
        self.assertEqual(publication, "LOCAL_ROOM_OK")


if __name__ == "__main__":
    unittest.main()
