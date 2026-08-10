from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agentsassemble.providers.local_openai import LocalOpenAICompatibleRuntime
from agentsassemble.providers.remote_http import RemoteResponseTooLarge
from agentsassemble.providers.room_portal import RoomPortal


def _stream(*chunks: dict[str, object]) -> io.BytesIO:
    body = "".join(
        f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        for chunk in chunks
    )
    return io.BytesIO(f"{body}data: [DONE]\n\n".encode())


class LocalOpenAIRoomObservationTests(unittest.TestCase):
    def test_default_transport_rejects_an_oversized_streaming_line(self) -> None:
        class OversizedStreamHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length") or "0"))
                body = b"data: " + (b"x" * (8 * 1_048_576 + 1))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), OversizedStreamHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            runtime = LocalOpenAICompatibleRuntime(
                "lmstudio",
                provider_name="LM Studio",
                model="local-model",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                message_source="lmstudio_sse",
            )
            runtime.send("hello")

            with self.assertRaises(RemoteResponseTooLarge):
                runtime.read_output(timeout_seconds=5)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

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
