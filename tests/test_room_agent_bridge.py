import threading
import time
import unittest

from agentsassemble.grok_acp_runtime import GrokAcpRuntime
from agentsassemble.room_agent_bridge import RoomAgentBridge, runtime_from_config


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

    def read_output(self, *, timeout_seconds, on_delta=None, on_activity=None):
        del timeout_seconds
        if on_activity:
            on_activity(
                {
                    "category": "command",
                    "status": "running",
                    "content": "cat /private/project/.env TOKEN=secret",
                }
            )
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


class DecliningRuntime(FakeRuntime):
    def read_output(self, *, timeout_seconds, on_delta=None, on_activity=None):
        del timeout_seconds, on_delta, on_activity
        return {"outcome": "decline", "reason_code": "nothing_useful_to_add"}


def _wait_for(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met")


class RoomAgentBridgeTests(unittest.TestCase):
    def test_grok_acp_config_selects_structured_runtime(self):
        runtime = runtime_from_config(
            {
                "participant_id": "grok",
                "provider_kind": "grok_live_session",
                "command": ["grok", "agent", "stdio"],
                "cwd": ".",
                "runtime_state_dir": ".agentsassemble/test-grok-acp",
            }
        )

        self.assertIsInstance(runtime, GrokAcpRuntime)

    def test_pty_runtime_preserves_an_intentional_empty_cli_argument(self):
        runtime = runtime_from_config(
            {
                "participant_id": "claude",
                "provider_kind": "claude_code",
                "command": ["claude", "--tools", "", "--safe-mode"],
                "cwd": ".",
            }
        )

        self.assertEqual(runtime.command, ["claude", "--tools", "", "--safe-mode"])

    def test_real_grok_command_does_not_fall_back_to_pty(self):
        with self.assertRaisesRegex(ValueError, "PTY fallback is disabled"):
            runtime_from_config(
                {
                    "participant_id": "grok",
                    "provider_kind": "grok_live_session",
                    "command": ["grok", "--no-alt-screen"],
                    "cwd": ".",
                }
            )

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
        activities = [payload for action, payload, _ in client.commands if action == "activity.update"]
        self.assertEqual(
            activities,
            [
                {
                    "turn_id": "turn-1",
                    "activity_kind": "tool",
                    "category": "command",
                    "status": "running",
                    "content": "명령 실행 중",
                },
                {
                    "turn_id": "turn-2",
                    "activity_kind": "tool",
                    "category": "command",
                    "status": "running",
                    "content": "명령 실행 중",
                },
            ],
        )
        self.assertNotIn("/private/project", str(activities))
        self.assertNotIn("TOKEN", str(activities))

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

    def test_structured_decline_does_not_emit_blank_final(self):
        client = FakeClient()
        runtime = DecliningRuntime()
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
        _wait_for(lambda: runtime.start_count == 1)
        client.messages.append(
            {"op": "turn.assign", "turn_id": "turn-decline", "provider_input": "observe", "timeout_seconds": 2}
        )
        _wait_for(lambda: any(action == "turn.decline" for action, _, _ in client.commands))
        client.messages.append({"op": "agent.control", "action": "stop"})
        thread.join(timeout=2)

        declines = [payload for action, payload, _ in client.commands if action == "turn.decline"]
        self.assertEqual(declines[0]["reason_code"], "nothing_useful_to_add")
        self.assertFalse(any(action == "message.final" for action, _, _ in client.commands))


if __name__ == "__main__":
    unittest.main()
