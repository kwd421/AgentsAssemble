import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agentsassemble.providers.opencode import OpenCodeRuntime
from agentsassemble.providers.remote_http import RemoteResponseTooLarge


class _Response:
    def __init__(self, *, lines=(), payload=None):
        self._lines = list(lines)
        self._payload = payload

    def __iter__(self):
        return iter(self._lines)

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def close(self):
        return None


def _event(event_type, properties):
    return ("data: " + json.dumps({"type": event_type, "properties": properties}) + "\n").encode()


class OpenCodeRuntimeTests(unittest.TestCase):
    def test_default_transport_rejects_an_oversized_json_response(self):
        class OversizedJsonHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(32 * 1_048_576 + 1))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b"{}")
                self.close_connection = True

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), OversizedJsonHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                runtime = OpenCodeRuntime(
                    "opencode",
                    endpoint=f"http://127.0.0.1:{server.server_port}",
                    workspace=temp_dir,
                    state_dir=Path(temp_dir) / "state",
                )

                with self.assertRaises(RemoteResponseTooLarge):
                    runtime.start()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_structured_progress_extends_the_inactivity_timeout(self):
        session_id = "session-1"
        state = {"request_message_id": ""}

        class SlowStructuredStream:
            def __iter__(self):
                message_id = "assistant-slow"
                events = [
                    _event(
                        "message.updated",
                        {
                            "sessionID": session_id,
                            "info": {
                                "id": message_id,
                                "parentID": state["request_message_id"],
                                "role": "assistant",
                            },
                        },
                    ),
                    _event(
                        "message.part.updated",
                        {
                            "sessionID": session_id,
                            "part": {"id": "text-slow", "messageID": message_id, "type": "text"},
                        },
                    ),
                    _event(
                        "message.part.delta",
                        {
                            "sessionID": session_id,
                            "messageID": message_id,
                            "partID": "text-slow",
                            "field": "text",
                            "delta": "done",
                        },
                    ),
                    _event("session.idle", {"sessionID": session_id}),
                ]
                for event in events:
                    time.sleep(0.6)
                    yield event

            def close(self):
                return None

        def opener(request, timeout):
            del timeout
            if "/event?" in request.full_url:
                return SlowStructuredStream()
            if "/prompt_async?" in request.full_url:
                state["request_message_id"] = json.loads(request.data.decode("utf-8"))["messageID"]
                return _Response(payload={})
            if "/message?" in request.full_url:
                return _Response(
                    payload=[
                        {
                            "info": {
                                "id": "assistant-slow",
                                "parentID": state["request_message_id"],
                                "role": "assistant",
                            },
                            "parts": [{"type": "text", "text": "done"}],
                        }
                    ]
                )
            raise AssertionError(request.full_url)

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = OpenCodeRuntime(
                "opencode",
                endpoint="http://127.0.0.1:1",
                workspace="/tmp",
                state_dir=Path(temp_dir),
                opener=opener,
            )
            runtime._session_id = session_id
            runtime._running = True
            runtime.send("slow turn")
            result = runtime.read_output(timeout_seconds=1.0)

        self.assertEqual(result["content"], "done")

    def test_routes_structured_permission_request_and_sends_exact_reply(self):
        session_id = "session-1"
        state = {"request_message_id": "", "reply": None}
        stream = _Response(lines=[])

        def opener(request, timeout):
            del timeout
            if "/event?" in request.full_url:
                return stream
            if "/prompt_async?" in request.full_url:
                state["request_message_id"] = json.loads(request.data.decode("utf-8"))["messageID"]
                stream._lines = [
                    _event(
                        "permission.asked",
                        {
                            "id": "per_1",
                            "sessionID": session_id,
                            "permission": "bash",
                            "patterns": ["git status --short"],
                            "metadata": {},
                            "always": ["git status *"],
                        },
                    ),
                    _event(
                        "message.updated",
                        {
                            "sessionID": session_id,
                            "info": {
                                "id": "assistant-1",
                                "parentID": state["request_message_id"],
                                "role": "assistant",
                            },
                        },
                    ),
                    _event(
                        "message.part.updated",
                        {
                            "sessionID": session_id,
                            "part": {"id": "text-1", "messageID": "assistant-1", "type": "text"},
                        },
                    ),
                    _event(
                        "message.part.delta",
                        {
                            "sessionID": session_id,
                            "messageID": "assistant-1",
                            "partID": "text-1",
                            "field": "text",
                            "delta": "done",
                        },
                    ),
                    _event("session.idle", {"sessionID": session_id}),
                ]
                return _Response(payload={})
            if "/permission/per_1/reply?" in request.full_url:
                state["reply"] = json.loads(request.data.decode("utf-8"))
                return _Response(payload=True)
            if "/message?" in request.full_url:
                return _Response(payload=[])
            raise AssertionError(request.full_url)

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = OpenCodeRuntime(
                "opencode",
                endpoint="http://127.0.0.1:1",
                workspace="/tmp",
                state_dir=Path(temp_dir),
                opener=opener,
            )
            runtime._session_id = session_id
            runtime._running = True
            requests = []

            def handle(request, respond):
                requests.append(request)
                respond({"option_id": "always"})

            runtime.set_request_handler(handle)
            runtime.send("inspect the repository")
            result = runtime.read_output(timeout_seconds=5)

        self.assertEqual(result["content"], "done")
        self.assertEqual(state["reply"], {"reply": "always"})
        self.assertEqual(requests[0]["request_kind"], "permission")
        self.assertEqual(
            [option["id"] for option in requests[0]["options"]],
            ["once", "always", "reject"],
        )

    def test_routes_structured_multi_question_answers_in_provider_order(self):
        session_id = "session-1"
        state = {"request_message_id": "", "reply": None}
        stream = _Response(lines=[])

        def opener(request, timeout):
            del timeout
            if "/event?" in request.full_url:
                return stream
            if "/prompt_async?" in request.full_url:
                state["request_message_id"] = json.loads(request.data.decode("utf-8"))["messageID"]
                stream._lines = [
                    _event(
                        "question.asked",
                        {
                            "id": "que_1",
                            "sessionID": session_id,
                            "questions": [
                                {
                                    "header": "검증",
                                    "question": "어떤 검증을 실행할까요?",
                                    "multiple": True,
                                    "custom": False,
                                    "options": [
                                        {"label": "단위 테스트", "description": "빠른 검증"},
                                        {"label": "빌드", "description": "구조 검증"},
                                    ],
                                }
                            ],
                        },
                    ),
                    _event(
                        "message.updated",
                        {
                            "sessionID": session_id,
                            "info": {
                                "id": "assistant-1",
                                "parentID": state["request_message_id"],
                                "role": "assistant",
                            },
                        },
                    ),
                    _event(
                        "message.part.updated",
                        {
                            "sessionID": session_id,
                            "part": {"id": "text-1", "messageID": "assistant-1", "type": "text"},
                        },
                    ),
                    _event(
                        "message.part.delta",
                        {
                            "sessionID": session_id,
                            "messageID": "assistant-1",
                            "partID": "text-1",
                            "field": "text",
                            "delta": "selected",
                        },
                    ),
                    _event("session.idle", {"sessionID": session_id}),
                ]
                return _Response(payload={})
            if "/question/que_1/reply?" in request.full_url:
                state["reply"] = json.loads(request.data.decode("utf-8"))
                return _Response(payload=True)
            if "/message?" in request.full_url:
                return _Response(payload=[])
            raise AssertionError(request.full_url)

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = OpenCodeRuntime(
                "opencode",
                endpoint="http://127.0.0.1:1",
                workspace="/tmp",
                state_dir=Path(temp_dir),
                opener=opener,
            )
            runtime._session_id = session_id
            runtime._running = True

            def handle(request, respond):
                self.assertTrue(request["questions"][0]["multiple"])
                respond({"answers": {"question-0": ["단위 테스트", "빌드"]}})

            runtime.set_request_handler(handle)
            runtime.send("choose checks")
            result = runtime.read_output(timeout_seconds=5)

        self.assertEqual(result["content"], "selected")
        self.assertEqual(state["reply"], {"answers": [["단위 테스트", "빌드"]]})

    def test_reports_original_structured_session_error_without_waiting_for_a_final(self):
        session_id = "session-1"
        stream = _Response(lines=[])

        def opener(request, timeout):
            del timeout
            if "/event?" in request.full_url:
                return stream
            if "/prompt_async?" in request.full_url:
                stream._lines = [
                    _event(
                        "session.error",
                        {
                            "sessionID": session_id,
                            "error": {
                                "name": "APIError",
                                "data": {
                                    "message": "Provider rejected the request before generation.",
                                    "isRetryable": False,
                                },
                            },
                        },
                    ),
                    _event("session.idle", {"sessionID": session_id}),
                ]
                return _Response(payload={})
            if "/message?" in request.full_url:
                return _Response(payload=[])
            raise AssertionError(request.full_url)

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = OpenCodeRuntime(
                "opencode",
                endpoint="http://127.0.0.1:1",
                workspace="/tmp",
                state_dir=Path(temp_dir),
                opener=opener,
            )
            runtime._session_id = session_id
            runtime._running = True
            runtime.send("reply")

            with self.assertRaisesRegex(
                RuntimeError,
                "Provider rejected the request before generation",
            ) as raised:
                runtime.read_output(timeout_seconds=5)

        self.assertNotIn("without a final assistant message", str(raised.exception))

    def test_late_previous_turn_events_do_not_complete_current_turn(self):
        session_id = "session-1"
        previous_message_id = "message-previous"
        current_message_id = "message-current"
        state = {"request_message_id": "", "current_available": False}

        class DelayedTurnStream:
            def __iter__(self):
                yield _event(
                    "message.updated",
                    {
                        "sessionID": session_id,
                        "info": {
                            "id": previous_message_id,
                            "parentID": "msg_previous_request",
                            "role": "assistant",
                        },
                    },
                )
                yield _event(
                    "message.part.updated",
                    {
                        "sessionID": session_id,
                        "part": {
                            "id": "reasoning-previous",
                            "messageID": previous_message_id,
                            "type": "reasoning",
                        },
                    },
                )
                yield _event(
                    "message.part.updated",
                    {
                        "sessionID": session_id,
                        "part": {
                            "id": "text-previous",
                            "messageID": previous_message_id,
                            "type": "text",
                        },
                    },
                )
                yield _event(
                    "message.part.delta",
                    {
                        "sessionID": session_id,
                        "messageID": previous_message_id,
                        "partID": "text-previous",
                        "field": "text",
                        "delta": "previous reply",
                    },
                )
                yield _event("session.idle", {"sessionID": session_id})
                state["current_available"] = True
                yield _event(
                    "message.updated",
                    {
                        "sessionID": session_id,
                        "info": {
                            "id": current_message_id,
                            "parentID": state["request_message_id"],
                            "role": "assistant",
                        },
                    },
                )
                yield _event(
                    "message.part.updated",
                    {
                        "sessionID": session_id,
                        "part": {
                            "id": "text-current",
                            "messageID": current_message_id,
                            "type": "text",
                        },
                    },
                )
                yield _event(
                    "message.part.delta",
                    {
                        "sessionID": session_id,
                        "messageID": current_message_id,
                        "partID": "text-current",
                        "field": "text",
                        "delta": "current reply",
                    },
                )
                yield _event("session.idle", {"sessionID": session_id})

            def close(self):
                return None

        def message_history():
            messages = [
                {
                    "info": {
                        "id": previous_message_id,
                        "parentID": "msg_previous_request",
                        "role": "assistant",
                    },
                    "parts": [{"type": "text", "text": "previous reply"}],
                }
            ]
            if state["current_available"]:
                messages.append(
                    {
                        "info": {
                            "id": current_message_id,
                            "parentID": state["request_message_id"],
                            "role": "assistant",
                        },
                        "parts": [{"type": "text", "text": "current reply"}],
                    }
                )
            return messages

        def opener(request, timeout):
            del timeout
            if "/event?" in request.full_url:
                return DelayedTurnStream()
            if "/prompt_async?" in request.full_url:
                payload = json.loads(request.data.decode("utf-8"))
                state["request_message_id"] = str(payload["messageID"])
                return _Response(payload={})
            if "/message?" in request.full_url:
                return _Response(payload=message_history())
            raise AssertionError(request.full_url)

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = OpenCodeRuntime(
                "opencode",
                endpoint="http://127.0.0.1:1",
                workspace="/tmp",
                state_dir=Path(temp_dir),
                opener=opener,
            )
            runtime._session_id = session_id
            runtime._running = True
            runtime.send("current turn")
            deltas = []
            activities = []

            result = runtime.read_output(
                timeout_seconds=5,
                on_delta=deltas.append,
                on_activity=activities.append,
            )

        self.assertEqual(result["content"], "current reply")
        self.assertEqual("".join(deltas), "current reply")
        self.assertEqual(activities, [])

    def test_keeps_reasoning_out_of_reply_stream_but_emits_public_activity(self):
        session_id = "session-1"
        message_id = "message-1"
        stream = _Response(
            lines=[
                _event(
                    "message.updated",
                    {
                        "sessionID": session_id,
                        "info": {
                            "id": message_id,
                            "parentID": "msg_current_request",
                            "role": "assistant",
                            "model": {"providerID": "opencode-go", "modelID": "glm-5.2"},
                        },
                    },
                ),
                _event(
                    "message.part.updated",
                    {
                        "sessionID": session_id,
                        "part": {
                            "id": "compact-1",
                            "messageID": "msg_current_request",
                            "type": "compaction",
                            "auto": True,
                        },
                    },
                ),
                _event("session.compacted", {"sessionID": session_id}),
                _event(
                    "message.part.updated",
                    {"sessionID": session_id, "part": {"id": "reasoning-1", "messageID": message_id, "type": "reasoning"}},
                ),
                _event(
                    "message.part.delta",
                    {"sessionID": session_id, "messageID": message_id, "partID": "reasoning-1", "field": "text", "delta": "hidden plan"},
                ),
                _event(
                    "message.part.updated",
                    {
                        "sessionID": session_id,
                        "part": {
                            "id": "tool-1",
                            "messageID": message_id,
                            "type": "tool",
                            "tool": "read_file",
                            "state": {"status": "running", "input": {"path": "/private/project/.env"}},
                        },
                    },
                ),
                _event(
                    "message.part.updated",
                    {
                        "sessionID": session_id,
                        "part": {
                            "id": "tool-1",
                            "messageID": message_id,
                            "type": "tool",
                            "tool": "read_file",
                            "state": {"status": "failed", "input": {"path": "/private/project/.env"}},
                        },
                    },
                ),
                _event(
                    "message.part.delta",
                    {"sessionID": session_id, "messageID": message_id, "partID": "text-1", "field": "text", "delta": "visible "},
                ),
                _event(
                    "message.part.updated",
                    {"sessionID": session_id, "part": {"id": "text-1", "messageID": message_id, "type": "text"}},
                ),
                _event(
                    "message.part.delta",
                    {"sessionID": session_id, "messageID": message_id, "partID": "text-1", "field": "text", "delta": "reply"},
                ),
                _event("session.idle", {"sessionID": session_id}),
            ]
        )
        final = _Response(
            payload=[
                {
                    "info": {"id": message_id, "parentID": "msg_current_request", "role": "assistant"},
                    "parts": [
                        {"type": "reasoning", "text": "hidden plan"},
                        {"type": "text", "text": "visible reply"},
                    ],
                }
            ]
        )

        def opener(request, timeout):
            del timeout
            if "/event?" in request.full_url:
                return stream
            if "/prompt_async?" in request.full_url:
                payload = json.loads(request.data.decode("utf-8"))
                request_message_id = str(payload["messageID"])
                stream._lines = [
                    line.replace(b"msg_current_request", request_message_id.encode("utf-8"))
                    for line in stream._lines
                ]
                final._payload[0]["info"]["parentID"] = request_message_id
                return _Response(payload={})
            if "/message?" in request.full_url:
                return final
            raise AssertionError(request.full_url)

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = OpenCodeRuntime(
                "opencode",
                endpoint="http://127.0.0.1:1",
                workspace="/tmp",
                state_dir=Path(temp_dir),
                opener=opener,
            )
            runtime._session_id = session_id
            runtime._running = True
            runtime.send("reply in the room")
            deltas = []
            activities = []

            result = runtime.read_output(
                timeout_seconds=5,
                on_delta=deltas.append,
                on_activity=activities.append,
            )

        self.assertEqual("".join(deltas), "visible reply")
        self.assertEqual(result["content"], "visible reply")
        self.assertEqual(result["metadata"]["observed_model_id"], "opencode-go/glm-5.2")
        self.assertNotIn("hidden plan", "".join(deltas))
        self.assertEqual(
            activities,
            [
                {"category": "compaction", "status": "started"},
                {"category": "compaction", "status": "completed"},
                {
                    "category": "reasoning",
                    "status": "running",
                    "activity_id": "reasoning-1",
                    "activity_title": "생각",
                    "activity_detail": "hidden plan",
                    "content": "hidden plan",
                },
                {
                    "category": "file_read",
                    "status": "running",
                    "activity_id": "tool-1",
                    "activity_title": "read_file",
                    "activity_detail": "[local path]/.env",
                    "content": "[local path]/.env",
                },
                {
                    "category": "file_read",
                    "status": "failed",
                    "activity_id": "tool-1",
                    "activity_title": "read_file",
                    "activity_detail": "[local path]/.env",
                    "content": "[local path]/.env",
                },
                {
                    "category": "reasoning",
                    "status": "completed",
                    "activity_id": "reasoning-1",
                    "activity_title": "생각",
                    "activity_detail": "hidden plan",
                    "content": "hidden plan",
                },
            ],
        )
        self.assertIn("hidden plan", str(activities))
        self.assertNotIn("/private/project", str(activities))


if __name__ == "__main__":
    unittest.main()
