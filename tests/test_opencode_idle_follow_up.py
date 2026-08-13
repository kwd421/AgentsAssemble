import json
import tempfile
import threading
import unittest
from pathlib import Path

from agentsassemble.providers.opencode import OpenCodeRuntime


class _Response:
    def __init__(self, *, payload=None):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def close(self):
        return None


def _event(event_type, properties):
    return ("data: " + json.dumps({"type": event_type, "properties": properties}) + "\n").encode()


class OpenCodeIdleFollowUpTests(unittest.TestCase):
    def test_idle_session_follow_up_wakes_and_returns_an_assistant_message(self):
        session_id = "session-1"
        prompt_started = threading.Event()
        state = {"request_message_id": ""}

        class IdleFollowUpStream:
            def __iter__(self):
                if not prompt_started.wait(timeout=0.5):
                    return
                yield _event(
                    "message.updated",
                    {
                        "sessionID": session_id,
                        "info": {"id": state["request_message_id"], "role": "user"},
                    },
                )
                message_id = "assistant-follow-up"
                yield _event(
                    "message.updated",
                    {
                        "sessionID": session_id,
                        "info": {
                            "id": message_id,
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
                            "id": "text-follow-up",
                            "messageID": message_id,
                            "type": "text",
                        },
                    },
                )
                yield _event("session.idle", {"sessionID": session_id})

            def close(self):
                return None

        def opener(request, timeout):
            del timeout
            if "/event?" in request.full_url:
                return IdleFollowUpStream()
            if "/prompt_async?" in request.full_url:
                return _Response(payload={})
            if "/message?" in request.full_url and request.get_method() == "POST":
                payload = json.loads(request.data.decode("utf-8"))
                self.assertNotIn("messageID", payload)
                state["request_message_id"] = "msg_server_generated_follow_up"
                prompt_started.set()
                return _Response(
                    payload={
                        "info": {
                            "id": "assistant-follow-up",
                            "parentID": state["request_message_id"],
                            "role": "assistant",
                        },
                        "parts": [{"type": "text", "text": "follow-up reply"}],
                    }
                )
            if "/message?" in request.full_url:
                return _Response(
                    payload=[
                        {
                            "info": {
                                "id": "assistant-follow-up",
                                "parentID": state["request_message_id"],
                                "role": "assistant",
                            },
                            "parts": [{"type": "text", "text": "follow-up reply"}],
                        }
                    ]
                )
            if "/abort?" in request.full_url:
                return _Response(payload=True)
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
            runtime.send("follow-up")

            result = runtime.read_output(timeout_seconds=5)

        self.assertEqual(result["content"], "follow-up reply")


if __name__ == "__main__":
    unittest.main()
