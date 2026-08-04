from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

from agentsassemble.providers.capabilities import ProviderCapabilityCatalog
from agentsassemble.providers.native_harness import NativeHarnessRuntime
from agentsassemble.providers.native_harness_gateway import NativeModelGateway


class _FailingDelegate:
    def start(self):
        raise RuntimeError("delegate startup failed")

    def health(self):
        return {"running": False}

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        del timeout_seconds


class _UpstreamServer:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []
        self.authorizations: list[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                owner.requests.append(json.loads(self.rfile.read(length)))
                owner.authorizations.append(self.headers.get("Authorization") or "")
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


class NativeHarnessCatalogTests(unittest.TestCase):
    def test_api_catalog_exposes_installed_native_coding_harnesses(self) -> None:
        available = {"codex", "claude"}
        catalog = ProviderCapabilityCatalog(
            runner=lambda _command, _timeout: (1, "", "not installed"),
            resolver=lambda executable: (
                f"/bin/{executable}" if executable in available else None
            ),
            remote_model_discovery=lambda _profile, _api_key: [],
            secret_resolver=lambda _provider_id: "",
        )

        deepseek = next(
            provider
            for provider in catalog.payload(refresh=True)
            if provider["id"] == "deepseek"
        )
        harness = next(
            control
            for control in deepseek["controls"]
            if control["key"] == "execution_harness"
        )

        self.assertEqual(
            [option["value"] for option in harness["options"]],
            ["builtin", "codex", "claude"],
        )


class NativeHarnessGatewayTests(unittest.TestCase):
    def test_codex_responses_request_round_trips_a_native_tool_call(self) -> None:
        upstream_response = {
            "id": "chat-1",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "run_command",
                                    "arguments": '{"command":"pwd"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        }
        with _UpstreamServer([upstream_response]) as upstream:
            gateway = NativeModelGateway(
                upstream_base_url=upstream.endpoint,
                upstream_api_key="secret-only-in-memory",
                model="deepseek-test",
                provider_kind="deepseek_api",
                max_output_tokens=4096,
            )
            gateway.start()
            try:
                response = _post(
                    f"{gateway.endpoint}/responses",
                    {
                        "model": "deepseek-test",
                        "instructions": "Use the tool.",
                        "input": [
                            {
                                "type": "message",
                                "role": "user",
                                "content": [{"type": "input_text", "text": "pwd"}],
                            }
                        ],
                        "tools": [
                            {
                                "type": "function",
                                "name": "run_command",
                                "description": "Run one command",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"command": {"type": "string"}},
                                    "required": ["command"],
                                },
                            }
                        ],
                        "stream": True,
                    },
                )
            finally:
                gateway.stop()

        events = _sse_events(response)
        tool_item = next(
            event["item"]
            for event in events
            if event["type"] == "response.output_item.done"
            and event.get("item", {}).get("type") == "function_call"
        )
        self.assertEqual(tool_item["name"], "run_command")
        self.assertEqual(tool_item["call_id"], "call-1")
        self.assertEqual(upstream.authorizations, ["Bearer secret-only-in-memory"])
        self.assertEqual(upstream.requests[0]["tools"][0]["function"]["name"], "run_command")

    def test_claude_messages_request_round_trips_native_tool_schema_and_text(self) -> None:
        upstream_response = {
            "id": "chat-2",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "finished"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
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
                    f"{gateway.endpoint}/messages",
                    {
                        "model": "deepseek-test",
                        "system": "Edit files when asked.",
                        "messages": [{"role": "user", "content": "finish"}],
                        "tools": [
                            {
                                "name": "Read",
                                "description": "Read a file",
                                "input_schema": {
                                    "type": "object",
                                    "properties": {"file_path": {"type": "string"}},
                                    "required": ["file_path"],
                                },
                            }
                        ],
                        "max_tokens": 1024,
                        "stream": True,
                    },
                )
            finally:
                gateway.stop()

        events = _sse_events(response)
        text_delta = next(
            event["delta"]["text"]
            for event in events
            if event["type"] == "content_block_delta"
            and event["delta"]["type"] == "text_delta"
        )
        self.assertEqual(text_delta, "finished")
        self.assertEqual(upstream.requests[0]["tools"][0]["function"]["name"], "Read")
        self.assertEqual(events[-1]["type"], "message_stop")

    def test_claude_nonstreaming_side_request_includes_top_level_usage(self) -> None:
        upstream_response = {
            "id": "chat-classifier",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "<severity>1</severity>",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 21, "completion_tokens": 4, "total_tokens": 25},
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
                response = _post_json(
                    f"{gateway.endpoint}/messages",
                    {
                        "model": "deepseek-test",
                        "messages": [
                            {"role": "user", "content": "Classify this safe command."}
                        ],
                        "max_tokens": 128,
                        "stream": False,
                    },
                )
            finally:
                gateway.stop()

        self.assertEqual(response["type"], "message")
        self.assertEqual(response["content"][0]["text"], "<severity>1</severity>")
        self.assertEqual(response["usage"]["input_tokens"], 21)
        self.assertEqual(response["usage"]["output_tokens"], 4)

    def test_delegate_start_failure_stops_the_internal_gateway(self) -> None:
        gateway = NativeModelGateway(
            upstream_base_url="https://api.example.test/v1",
            upstream_api_key="secret",
            model="vendor/model",
            provider_kind="custom_openai_api",
        )
        runtime = NativeHarnessRuntime(
            _FailingDelegate(),
            harness="codex",
            gateway=gateway,
        )

        with self.assertRaisesRegex(RuntimeError, "delegate startup failed"):
            runtime.start()

        self.assertIsNone(gateway.pid)


def _post(url: str, payload: dict[str, object]) -> str:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    with urlopen(request, timeout=5.0) as response:
        return response.read().decode("utf-8")


def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    return json.loads(_post(url, payload))


def _sse_events(body: str) -> list[dict[str, object]]:
    return [
        json.loads(line[5:].strip())
        for line in body.splitlines()
        if line.startswith("data:")
    ]


if __name__ == "__main__":
    unittest.main()
