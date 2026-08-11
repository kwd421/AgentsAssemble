from __future__ import annotations

import json
import io
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from agentsassemble.providers.native_harness import NativeHarnessRuntime
from agentsassemble.providers.native_harness import native_harness_runtime
from agentsassemble.providers.native_harness_gateway import NativeModelGateway
from agentsassemble.providers.harness_pi import PiHarnessRuntime


class _RunningProcess:
    def __init__(self) -> None:
        self.stdin = io.StringIO()
        self.pid = 1234

    def poll(self):
        return None


class _FakeGateway:
    def start(self) -> None:
        pass

    def health(self) -> dict[str, object]:
        return {}


class _FailingDelegate:
    def start(self):
        raise RuntimeError("delegate startup failed")

    def health(self):
        return {"running": False}

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        del timeout_seconds


class _FakeClaudeTerminalRuntime:
    def __init__(self, agent_id: str, command: list[str], **kwargs: object) -> None:
        self.agent_id = agent_id
        self.command = list(command)
        self.environment = dict(kwargs.get("env") or {})
        self.running = False

    def start(self) -> dict[str, object]:
        self.running = True
        return self.health()

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        del timeout_seconds
        self.running = False

    def health(self) -> dict[str, object]:
        return {"running": self.running, "runtime_kind": "live_cli"}


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


class NativeHarnessGatewayTests(unittest.TestCase):
    def test_loopback_gateway_rejects_callers_without_its_process_capability(self) -> None:
        with _UpstreamServer([]) as upstream:
            gateway = NativeModelGateway(
                upstream_base_url=upstream.endpoint,
                upstream_api_key="billable-secret",
                model="deepseek-test",
                provider_kind="deepseek_api",
            )
            gateway.start()
            origin = urlsplit(gateway.endpoint)
            unauthenticated_url = f"{origin.scheme}://{origin.netloc}/v1/models"
            try:
                with self.assertRaises(HTTPError) as caught:
                    urlopen(unauthenticated_url, timeout=5.0)
            finally:
                gateway.stop()

        self.assertEqual(caught.exception.code, 401)
        self.assertEqual(upstream.requests, [])

    def test_loopback_gateway_rejects_an_oversized_body_before_upstream_spend(self) -> None:
        with _UpstreamServer([]) as upstream:
            gateway = NativeModelGateway(
                upstream_base_url=upstream.endpoint,
                upstream_api_key="billable-secret",
                model="deepseek-test",
                provider_kind="deepseek_api",
                context_contract_bytes=65_536,
            )
            gateway.start()
            endpoint = urlsplit(gateway.endpoint)
            connection = HTTPConnection(endpoint.hostname, endpoint.port, timeout=5.0)
            try:
                connection.putrequest("POST", f"{endpoint.path}/responses")
                connection.putheader("Content-Type", "application/json")
                connection.putheader("Content-Length", "1100000")
                connection.endheaders()
                response = connection.getresponse()
                response.read()
            finally:
                connection.close()
                gateway.stop()

        self.assertEqual(response.status, 413)
        self.assertEqual(upstream.requests, [])

    def test_opencode_chat_stream_reaches_the_upstream_and_preserves_tool_calls(self) -> None:
        upstream_response = {
            "id": "chat-opencode",
            "model": "deepseek-test",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-edit",
                                "type": "function",
                                "function": {
                                    "name": "edit",
                                    "arguments": '{"path":"input.txt"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
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
                request = Request(
                    f"{gateway.endpoint}/chat/completions",
                    data=json.dumps(
                        {
                            "model": "client-alias",
                            "messages": [{"role": "user", "content": "edit it"}],
                            "tools": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "edit",
                                        "parameters": {"type": "object"},
                                    },
                                }
                            ],
                            "stream": True,
                            "stream_options": {"include_usage": True},
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=5.0) as response:
                    body = response.read().decode("utf-8")
            finally:
                gateway.stop()

        self.assertFalse(upstream.requests[0]["stream"])
        self.assertNotIn("stream_options", upstream.requests[0])
        self.assertEqual(upstream.requests[0]["model"], "deepseek-test")
        self.assertIn('"name":"edit"', body)
        self.assertIn('"finish_reason":"tool_calls"', body)
        self.assertTrue(body.endswith("data: [DONE]\n\n"))

    def test_pi_waits_for_the_agent_after_an_intermediate_tool_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = PiHarnessRuntime(
                agent_id="pi-test",
                executable="pi",
                workspace=temp_dir,
                state_dir=Path(temp_dir) / "state",
                model="deepseek-test",
                gateway=_FakeGateway(),
            )
            runtime._process = _RunningProcess()
            runtime._running = True
            runtime._pending = "Edit the file and report completion."
            runtime._events.put(
                {
                    "type": "turn_end",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "toolCall",
                                "id": "call-edit",
                                "name": "edit",
                            }
                        ],
                    },
                }
            )
            runtime._events.put(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "delta": "PI_DONE",
                    },
                }
            )
            runtime._events.put(
                {
                    "type": "turn_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "PI_DONE"}],
                    },
                }
            )
            runtime._events.put({"type": "agent_end", "messages": []})
            runtime._events.put({"type": "agent_settled"})

            result = runtime.read_output(timeout_seconds=1.0)

        self.assertEqual(result["content"], "PI_DONE")

    def test_codex_gateway_compacts_only_tool_results_delivered_before_this_request(self) -> None:
        upstream_responses = [
            {
                "id": "chat-first",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "continue"},
                        "finish_reason": "stop",
                    }
                ],
            },
            {
                "id": "chat-second",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "finished"},
                        "finish_reason": "stop",
                    }
                ],
            },
        ]
        first_output = "a" * 9_000
        second_output = "b" * 9_000
        with _UpstreamServer(upstream_responses) as upstream:
            gateway = NativeModelGateway(
                upstream_base_url=upstream.endpoint,
                upstream_api_key="secret",
                model="deepseek-test",
                provider_kind="deepseek_api",
                context_contract_bytes=65_536,
            )
            gateway.start()
            try:
                _post(
                    f"{gateway.endpoint}/responses",
                    _codex_tool_history([("call-1", first_output)]),
                )
                _post(
                    f"{gateway.endpoint}/responses",
                    _codex_tool_history(
                        [("call-1", first_output), ("call-2", second_output)]
                    ),
                )
                health = gateway.health()
            finally:
                gateway.stop()

        second_messages = upstream.requests[1]["messages"]
        first_result = next(
            message
            for message in second_messages
            if message.get("tool_call_id") == "call-1"
        )
        latest_result = next(
            message
            for message in second_messages
            if message.get("tool_call_id") == "call-2"
        )
        self.assertIn("delivered_tool_result_elided", first_result["content"])
        self.assertEqual(latest_result["content"], second_output)
        self.assertEqual(health["compacted_tool_result_count"], 1)

    def test_claude_api_harness_routes_a_native_permission_through_the_room(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            terminals: list[_FakeClaudeTerminalRuntime] = []

            def terminal_runtime(
                agent_id: str,
                command: list[str],
                **kwargs: object,
            ) -> _FakeClaudeTerminalRuntime:
                terminal = _FakeClaudeTerminalRuntime(agent_id, command, **kwargs)
                terminals.append(terminal)
                return terminal

            with (
                patch(
                    "agentsassemble.providers.native_harness.shutil.which",
                    return_value="/fake/claude",
                ),
                patch(
                    "agentsassemble.providers.native_harness.LiveCliRuntime",
                    new=terminal_runtime,
                ),
            ):
                runtime = native_harness_runtime(
                    agent_id="deepseek-with-claude",
                    harness="claude",
                    runtime_kind="api",
                    provider_kind="deepseek_api",
                    provider_endpoint="https://api.example.test/v1",
                    credential="secret",
                    model="deepseek-test",
                    reasoning_effort="low",
                    permission_mode="workspace_write",
                    service_tier="default",
                    workspace=str(root / "workspace"),
                    runtime_state_dir=str(root / "provider-state"),
                    environment={},
                    room_portal=None,
                )
                requests: list[dict[str, object]] = []

                def allow_once(request: dict[str, object], respond) -> None:
                    requests.append(request)
                    respond({"option_id": "allow-once"})

                runtime.set_request_handler(allow_once)
                health = runtime.start()
                self.assertEqual(health["runtime_kind"], "api")
                settings_path = Path(str(health["provider_request_settings_path"]))
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
                endpoint = settings["hooks"]["PermissionRequest"][0]["hooks"][0]["url"]
                token = terminals[0].environment["AGENTSASSEMBLE_CLAUDE_HOOK_TOKEN"]
                try:
                    result = _post_authenticated_json(
                        endpoint,
                        token,
                        {
                            "hook_event_name": "PermissionRequest",
                            "tool_name": "Bash",
                            "tool_input": {"command": "git status --short"},
                        },
                    )
                finally:
                    runtime.stop()

        self.assertEqual(result["hookSpecificOutput"]["decision"]["behavior"], "allow")
        self.assertEqual(requests[0]["request_kind"], "permission")
        self.assertFalse(settings_path.exists())

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
            runtime_kind="api",
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


def _codex_tool_history(results: list[tuple[str, str]]) -> dict[str, object]:
    items: list[dict[str, object]] = [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Inspect the files."}],
        }
    ]
    for index, (call_id, output) in enumerate(results, start=1):
        items.extend(
            [
                {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": "read_file",
                    "arguments": json.dumps({"path": f"file-{index}.txt"}),
                },
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                },
            ]
        )
    return {
        "model": "deepseek-test",
        "instructions": "Inspect files and report the result.",
        "input": items,
        "stream": True,
    }


def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    return json.loads(_post(url, payload))


def _post_authenticated_json(
    url: str,
    token: str,
    payload: dict[str, object],
) -> dict[str, object]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=5.0) as response:
        return json.loads(response.read().decode("utf-8"))


def _sse_events(body: str) -> list[dict[str, object]]:
    return [
        json.loads(line[5:].strip())
        for line in body.splitlines()
        if line.startswith("data:")
    ]


if __name__ == "__main__":
    unittest.main()
