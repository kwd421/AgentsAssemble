import threading
import time
import unittest

from agentsassemble.grok_acp_runtime import GrokAcpRuntime
from agentsassemble.provider_runtime_contracts import AdapterContractError, ProviderTurnResult
from agentsassemble.room_agent_bridge import (
    BridgeConfigError,
    CanonicalBridgeLaunchConfig,
    RoomAgentBridge,
    runtime_from_config,
)


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
            "outcome": "message",
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
            "provider_session_active": True,
            "is_one_shot": False,
            "resolved_executable": "/fake/codex",
            "started_at": "2026-01-01T00:00:00+00:00",
        }


class DecliningRuntime(FakeRuntime):
    def read_output(self, *, timeout_seconds, on_delta=None, on_activity=None):
        del timeout_seconds, on_delta, on_activity
        return {"outcome": "decline", "reason_code": "nothing_useful_to_add"}


class InvalidDecliningRuntime(FakeRuntime):
    def read_output(self, *, timeout_seconds, on_delta=None, on_activity=None):
        del timeout_seconds, on_delta, on_activity
        return {"outcome": "decline"}


class InvalidHealthAfterStartRuntime(FakeRuntime):
    def __init__(self):
        super().__init__()
        self.health_count = 0

    def health(self):
        self.health_count += 1
        if self.health_count == 1:
            return super().health()
        return {"running": True, "provider_session_active": True}


def _launch_config(**overrides):
    values = {
        "room_id": "general",
        "participant_id": "codex",
        "session_id": "codex",
        "provider_kind": "codex_live_session",
        "command": ["codex"],
        "cwd": ".",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "low",
        "service_tier": "default",
        "variant": "",
        "permission_mode": "meeting_read_only",
        "transport": "pty",
        "quiet_seconds": 4.0,
        "input_mode": "line",
        "submit_newline": "\r",
        "submit_delay_seconds": 0.1,
        "terminal_rows": 40,
        "terminal_columns": 120,
        "startup_quiet_seconds": 1.0,
        "startup_timeout_seconds": 20.0,
        "startup_accept_contains": "",
        "startup_accept_keys": "\r",
        "startup_input": "",
        "turn_timeout_seconds": 180.0,
        "runtime_profile_key": "test-profile",
        "runtime_state_dir": ".agentsassemble/test-provider-state",
        "credential_stdin": False,
        "provider_endpoint": "",
        "provider_server_pid": None,
    }
    values.update(overrides)
    return values


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
            _launch_config(
                participant_id="grok",
                session_id="grok",
                provider_kind="grok_live_session",
                command=["grok", "agent", "stdio"],
                model="grok-4.5",
                transport="acp_stdio",
                runtime_state_dir=".agentsassemble/test-grok-acp",
            )
        )

        self.assertIsInstance(runtime, GrokAcpRuntime)

    def test_pty_runtime_preserves_an_intentional_empty_cli_argument(self):
        runtime = runtime_from_config(
            _launch_config(
                participant_id="claude",
                session_id="claude",
                provider_kind="claude_code",
                command=["claude", "--tools", "", "--safe-mode"],
                model="claude-sonnet-4-6",
            )
        )

        self.assertEqual(runtime.command, ["claude", "--tools", "", "--safe-mode"])

    def test_real_grok_command_does_not_fall_back_to_pty(self):
        with self.assertRaisesRegex(ValueError, "PTY fallback is disabled"):
            runtime_from_config(
                _launch_config(
                    participant_id="grok",
                    session_id="grok",
                    provider_kind="grok_live_session",
                    command=["grok", "--no-alt-screen"],
                    model="grok-4.5",
                    transport="acp_stdio",
                )
            )

    def test_canonical_bridge_config_rejects_missing_profile_and_transport(self):
        for missing in ("model", "transport", "cwd", "turn_timeout_seconds"):
            with self.subTest(missing=missing):
                values = _launch_config()
                values.pop(missing)
                with self.assertRaises(BridgeConfigError) as raised:
                    CanonicalBridgeLaunchConfig.parse_strict(values)
                self.assertEqual(raised.exception.code, "bridge_config_invalid")

    def test_canonical_bridge_config_preserves_empty_cli_arguments_and_profile_values(self):
        parsed = CanonicalBridgeLaunchConfig.parse_strict(
            _launch_config(
                participant_id="claude",
                session_id="claude",
                provider_kind="claude_code",
                command=["claude", "--tools", "", "--safe-mode"],
                model="claude-sonnet-4-6",
                reasoning_effort="",
                service_tier="",
            )
        )

        self.assertEqual(parsed.command[2], "")
        self.assertEqual(parsed.reasoning_effort, "")
        self.assertEqual(parsed.service_tier, "")

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

    def test_decline_without_reason_is_an_adapter_contract_error(self):
        client = FakeClient()
        runtime = InvalidDecliningRuntime()
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
            {"op": "turn.assign", "turn_id": "turn-invalid-decline", "provider_input": "observe"}
        )
        _wait_for(lambda: any(action == "turn.failed" for action, _, _ in client.commands))
        client.messages.append({"op": "agent.control", "action": "stop"})
        thread.join(timeout=2)

        failure = next(payload for action, payload, _ in client.commands if action == "turn.failed")
        self.assertEqual(failure["error_code"], "adapter_contract_error")
        self.assertIn("reason_code", failure["message"])
        self.assertFalse(any(action == "turn.decline" for action, _, _ in client.commands))

    def test_result_outcome_is_required_at_the_top_level(self):
        with self.assertRaises(AdapterContractError) as raised:
            ProviderTurnResult.parse(
                {
                    "content": "looks valid",
                    "metadata": {"outcome": "message"},
                }
            )

        self.assertEqual(raised.exception.code, "adapter_contract_error")

    def test_invalid_health_does_not_fabricate_final_diagnostics(self):
        client = FakeClient()
        runtime = InvalidHealthAfterStartRuntime()
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
            {"op": "turn.assign", "turn_id": "turn-invalid-health", "provider_input": "respond"}
        )
        _wait_for(lambda: any(action == "turn.failed" for action, _, _ in client.commands))
        client.messages.append({"op": "agent.control", "action": "stop"})
        thread.join(timeout=2)

        failure = next(payload for action, payload, _ in client.commands if action == "turn.failed")
        self.assertEqual(failure["error_code"], "adapter_contract_error")
        self.assertTrue(failure["diagnostics"]["adapter_health_invalid"])
        self.assertNotIn("started_at", failure["diagnostics"])
        self.assertFalse(any(action == "message.final" for action, _, _ in client.commands))


if __name__ == "__main__":
    unittest.main()
