from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

from agentsassemble.providers.codex_app_server_live import CodexAppServerLiveRuntime
from agentsassemble.providers.native_harness_gateway import NativeModelGateway


class _UpstreamServer:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                owner.requests.append(json.loads(self.rfile.read(length)))
                body = json.dumps(owner.responses.pop(0)).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *args: object) -> None:
                del args

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def endpoint(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}/v1"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)


def _post(url: str, payload: dict[str, object]) -> str:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    with urlopen(request, timeout=5.0) as response:
        return response.read().decode("utf-8")


def _sse_events(body: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: {")
    ]


class CodexNativeGatewayContractTests(unittest.TestCase):
    def test_room_namespace_survives_the_chat_completions_boundary(self) -> None:
        upstream_response = {
            "id": "chat-room-tool",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "I should read the shared room.",
                    "tool_calls": [{
                        "id": "call-room-read",
                        "type": "function",
                        "function": {
                            "name": "mcp__agentsassemble_room__read_discussion",
                            "arguments": "{}",
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        }
        with _UpstreamServer([upstream_response]) as upstream:
            gateway = NativeModelGateway(
                upstream_base_url=upstream.endpoint,
                upstream_api_key="secret",
                model="deepseek-test",
                provider_kind="deepseek_api",
            )
            gateway.start()
            try:
                response = _post(
                    f"{gateway.endpoint}/responses",
                    {
                        "model": "deepseek-test",
                        "input": [{
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "Read the room."}],
                        }],
                        "tools": [
                            {
                                "type": "namespace",
                                "name": "mcp__agentsassemble_room",
                                "description": "Room tools",
                                "tools": [{
                                    "type": "function",
                                    "name": "read_discussion",
                                    "description": "Read the room",
                                    "parameters": {
                                        "type": "object",
                                        "properties": {},
                                        "additionalProperties": False,
                                    },
                                }],
                            },
                            {
                                "type": "namespace",
                                "name": "mcp__ambient_private_server",
                                "tools": [{
                                    "type": "function",
                                    "name": "read_private_data",
                                    "parameters": {"type": "object", "properties": {}},
                                }],
                            },
                        ],
                        "stream": True,
                    },
                )
            finally:
                gateway.stop()

        self.assertEqual(
            [tool["function"]["name"] for tool in upstream.requests[0]["tools"]],
            ["mcp__agentsassemble_room__read_discussion"],
        )
        events = _sse_events(response)
        reasoning_added = next(
            index for index, event in enumerate(events)
            if event["type"] == "response.output_item.added"
            and event.get("item", {}).get("type") == "reasoning"
        )
        reasoning_part = next(
            index for index, event in enumerate(events)
            if event["type"] == "response.reasoning_summary_part.added"
        )
        self.assertLess(reasoning_added, reasoning_part)
        tool_item = next(
            event["item"] for event in events
            if event["type"] == "response.output_item.done"
            and event.get("item", {}).get("type") == "function_call"
        )
        self.assertEqual(tool_item["namespace"], "mcp__agentsassemble_room")
        self.assertEqual(tool_item["name"], "read_discussion")

    def test_parallel_tool_calls_remain_one_assistant_transaction(self) -> None:
        upstream_response = {
            "id": "chat-after-parallel-tools",
            "choices": [{
                "message": {"role": "assistant", "content": "finished"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }
        with _UpstreamServer([upstream_response]) as upstream:
            gateway = NativeModelGateway(
                upstream_base_url=upstream.endpoint,
                upstream_api_key="secret",
                model="deepseek-test",
                provider_kind="deepseek_api",
            )
            gateway.start()
            try:
                _post(
                    f"{gateway.endpoint}/responses",
                    {
                        "model": "deepseek-test",
                        "input": [
                            {"type": "message", "role": "user", "content": "Read both."},
                            {"type": "function_call", "call_id": "call-room", "name": "read_discussion", "namespace": "mcp__agentsassemble_room", "arguments": "{}"},
                            {"type": "function_call", "call_id": "call-file", "name": "exec_command", "arguments": '{"cmd":"cat PROBE.txt"}'},
                            {"type": "function_call_output", "call_id": "call-room", "output": "room state"},
                            {"type": "function_call_output", "call_id": "call-file", "output": "ORBIT-7421"},
                        ],
                        "stream": True,
                    },
                )
            finally:
                gateway.stop()

        messages = upstream.requests[0]["messages"]
        assistant = next(message for message in messages if message["role"] == "assistant")
        self.assertEqual(
            [call["id"] for call in assistant["tool_calls"]],
            ["call-room", "call-file"],
        )
        self.assertEqual(
            [message["tool_call_id"] for message in messages if message["role"] == "tool"],
            ["call-room", "call-file"],
        )

    def test_primary_provider_error_is_not_buried_by_diagnostics(self) -> None:
        class FailingAppServer:
            def send_turn(self, handle, packet):
                del handle, packet
                return iter([{
                    "type": "error",
                    "diagnostics": [
                        {
                            "setting": "app_server",
                            "status": "failed",
                            "message": "Upstream rejected the third model request.",
                        },
                        *[
                            {
                                "setting": f"noise-{index}",
                                "status": "recorded",
                                "message": "x" * 200,
                            }
                            for index in range(30)
                        ],
                    ],
                }])

            def diagnose(self, handle):
                del handle
                return {}

        runtime = CodexAppServerLiveRuntime(
            "codex-guest",
            workspace="/tmp/room",
            model="deepseek-v4-flash",
            reasoning_effort="high",
            permission_mode="meeting_read_only",
        )
        runtime.runtime = FailingAppServer()
        runtime.pending = "hello"

        with self.assertRaises(RuntimeError) as caught:
            runtime.read_output(timeout_seconds=2)
        self.assertEqual(
            str(caught.exception),
            "Upstream rejected the third model request.",
        )


if __name__ == "__main__":
    unittest.main()
