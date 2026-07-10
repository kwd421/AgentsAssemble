import threading
import time
import unittest

from agentsassemble.room_agent_bridge import RoomAgentBridge


class FakeClient:
    def __init__(self):
        self.messages = []
        self.commands = []
        self.closed = False
        self._lock = threading.Lock()

    def receive(self):
        with self._lock:
            messages = list(self.messages)
            self.messages.clear()
            return messages

    def command(self, action, payload=None, *, request_id=""):
        with self._lock:
            self.commands.append((action, dict(payload or {}), request_id))
        return request_id

    def close(self):
        self.closed = True


class FakeRuntime:
    def __init__(self):
        self.start_count = 0
        self.stop_count = 0
        self.sent = []
        self.interrupted = False

    def start(self):
        self.start_count += 1
        return self.health()

    def send(self, text):
        self.sent.append(text)

    def read_output(self, *, timeout_seconds, on_delta=None):
        del timeout_seconds
        if on_delta:
            on_delta("clean ")
            on_delta("delta")
        return {
            "content": "clean final",
            "metadata": {"message_source": "fake-transcript"},
        }

    def interrupt(self):
        self.interrupted = True

    def stop(self, *, timeout_seconds=2.0):
        del timeout_seconds
        self.stop_count += 1

    def health(self):
        return {
            "pid": 4242,
            "running": True,
            "pty": True,
            "transport": "pty",
            "is_one_shot": False,
            "resolved_executable": "/fake/codex",
            "started_at": "2026-01-01T00:00:00+00:00",
        }


def _wait_for(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met")


class RoomAgentBridgeTests(unittest.TestCase):
    def test_persistent_runtime_handles_multiple_turns_without_restart(self):
        client = FakeClient()
        runtime = FakeRuntime()
        bridge = RoomAgentBridge(
            client,
            runtime,
            room_id="general",
            participant_id="codex",
            session_id="codex",
            receive_sleep_seconds=0.005,
        )
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        _wait_for(lambda: any(action == "bridge.ready" for action, _, _ in client.commands))

        client.messages.append(
            {
                "op": "turn.assign",
                "turn_id": "turn-1",
                "provider_input": "first prompt",
                "timeout_seconds": 2,
            }
        )
        _wait_for(lambda: len([item for item in client.commands if item[0] == "message.final"]) == 1)
        client.messages.append(
            {
                "op": "turn.assign",
                "turn_id": "turn-2",
                "provider_input": "second prompt",
                "timeout_seconds": 2,
            }
        )
        _wait_for(lambda: len([item for item in client.commands if item[0] == "message.final"]) == 2)
        client.messages.append({"op": "agent.control", "action": "stop"})
        thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(runtime.start_count, 1)
        self.assertEqual(runtime.sent, ["first prompt", "second prompt"])
        self.assertEqual(runtime.stop_count, 1)
        deltas = [payload["content"] for action, payload, _ in client.commands if action == "message.delta"]
        self.assertEqual(deltas, ["clean ", "delta", "clean ", "delta"])
        finals = [payload for action, payload, _ in client.commands if action == "message.final"]
        self.assertEqual([payload["content"] for payload in finals], ["clean final", "clean final"])
        self.assertTrue(all(payload["message_source"] == "fake-transcript" for payload in finals))

    def test_interrupt_is_forwarded_without_stopping_runtime(self):
        client = FakeClient()
        runtime = FakeRuntime()
        bridge = RoomAgentBridge(client, runtime, room_id="general", participant_id="codex", session_id="codex")
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        _wait_for(lambda: runtime.start_count == 1)
        client.messages.append({"op": "agent.control", "action": "interrupt"})
        _wait_for(lambda: runtime.interrupted)
        client.messages.append({"op": "agent.control", "action": "stop"})
        thread.join(timeout=2)

        self.assertTrue(runtime.interrupted)
        self.assertEqual(runtime.start_count, 1)


if __name__ == "__main__":
    unittest.main()
