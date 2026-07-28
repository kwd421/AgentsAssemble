import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.providers.opencode import OpenCodeRuntime


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

    def test_streams_text_parts_but_never_reasoning_parts(self):
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
                {"category": "reasoning", "status": "running"},
                {"category": "file_read", "status": "running"},
                {"category": "reasoning", "status": "completed"},
            ],
        )
        self.assertNotIn("/private/project", str(activities))


if __name__ == "__main__":
    unittest.main()
