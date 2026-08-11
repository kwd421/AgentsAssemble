from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from agentsassemble.providers.agent_bridge import RoomAgentBridge
from agentsassemble.providers.room_portal import RoomPortal
from plugins.rimworld.server.sim import ColonySimulation


class PluginBridgeClient:
    def __init__(
        self,
        messages: list[dict[str, object]],
        *,
        reject_plugin: bool = False,
    ) -> None:
        self.messages = list(messages)
        self.reject_plugin = reject_plugin
        self.commands: list[tuple[str, dict[str, object], str]] = []
        self.plugin_commands: list[dict[str, object]] = []
        self.closed = False
        self._lock = threading.Lock()

    def receive(self) -> list[dict[str, object]]:
        with self._lock:
            messages = list(self.messages)
            self.messages.clear()
            return messages

    def command(
        self,
        action: str,
        payload: dict[str, object] | None = None,
        *,
        request_id: str = "",
    ) -> str:
        with self._lock:
            body = dict(payload or {})
            self.commands.append((action, body, request_id))
            response: dict[str, object] = {
                "op": "ack",
                "request_id": request_id,
                "accepted": True,
            }
            if action == "room.observed":
                response["result"] = {
                    "observed_through_seq": int(body.get("through_seq") or 0)
                }
            self.messages.append(response)
        return request_id

    def plugin(
        self,
        plugin_id: str,
        action: str,
        args: dict[str, object] | None = None,
        *,
        revision: str = "",
        request_id: str = "",
    ) -> str:
        with self._lock:
            self.plugin_commands.append(
                {
                    "plugin_id": plugin_id,
                    "action": action,
                    "args": dict(args or {}),
                    "revision": revision,
                    "request_id": request_id,
                }
            )
            self.messages.append(
                {
                    "op": "plugin_nack" if self.reject_plugin else "plugin_ack",
                    "request_id": request_id,
                    "error": {
                        "code": "command_failed",
                        "message": "Invalid colony action.",
                    }
                    if self.reject_plugin
                    else None,
                }
            )
        return request_id

    def close(self) -> None:
        self.closed = True


class PluginBridgeRuntime:
    def __init__(self, portal: RoomPortal, *, fail: bool = False) -> None:
        self.portal = portal
        self.fail = fail

    def start(self) -> dict[str, object]:
        return self.health()

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        del timeout_seconds

    def interrupt(self) -> None:
        return None

    def health(self) -> dict[str, object]:
        return {
            "pid": 4242,
            "running": True,
            "pty": True,
            "transport": "pty",
            "provider_session_active": True,
            "is_one_shot": False,
            "resolved_executable": "/fake/provider",
            "started_at": "2026-01-01T00:00:00+00:00",
        }

    def send(self, _text: str) -> None:
        self.portal.acp_read_text("/agentsassemble-room/current.md")
        self.portal.activity_plugin_observe()
        if not self.fail:
            self.portal.activity_plugin_act("eat", {})
            self.portal.activity_plugin_speak("식량부터 확인하겠습니다.")

    def read_output(self, *, timeout_seconds, on_delta=None, on_activity=None):
        del timeout_seconds, on_delta, on_activity
        if self.fail:
            raise RuntimeError("provider unavailable")
        return {
            "outcome": "message",
            "content": "private provider final",
            "metadata": {"observed_model_id": "plugin-test-model"},
        }


def _wake_frames(snapshot: dict[str, object], turn_id: str) -> list[dict[str, object]]:
    return [
        {
            "room_settings": {"activity_plugin": "rimworld"},
            "participants": [
                {"participant_id": value, "participant_type": "agent"}
                for value in ("agent-a", "agent-b", "agent-c")
            ],
        },
        {
            "op": "event",
            "stream": "plugin",
            "events": [
                {
                    "type": "plugin.snapshot",
                    "plugin_id": "rimworld",
                    "payload": snapshot,
                }
            ],
        },
        {
            "op": "room.wake",
            "room_id": "general",
            "participant_id": "agent-b",
            "session_id": "agent-b",
            "turn_id": turn_id,
            "source_event_id": f"event-{turn_id}",
            "input_up_to_seq": 0,
            "attachment_ids": [],
            "observation_kind": "ambient_observation",
            "publication_mode": "explicit_room_portal",
            "timeout_seconds": 2,
        },
    ]


def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met")


class RoomAgentBridgeActivityPluginTests(unittest.TestCase):
    def _run(
        self,
        *,
        fail: bool,
        reject_plugin: bool = False,
    ) -> tuple[PluginBridgeClient, dict[str, object]]:
        snapshot = ColonySimulation(seed=12).snapshot()
        turn_id = "wake-plugin-failure" if fail else "wake-plugin"
        client = PluginBridgeClient(
            _wake_frames(snapshot, turn_id),
            reject_plugin=reject_plugin,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="agent-b")
            portal.prepare()
            bridge = RoomAgentBridge(
                client,
                PluginBridgeRuntime(portal, fail=fail),
                room_id="general",
                participant_id="agent-b",
                session_id="agent-b",
                receive_sleep_seconds=0.005,
                room_portal=portal,
            )
            thread = threading.Thread(target=bridge.run, daemon=True)
            thread.start()
            terminal_action = "turn.failed" if fail or reject_plugin else "turn.decline"
            _wait_for(
                lambda: any(action == terminal_action for action, _, _ in client.commands)
            )
            with client._lock:
                client.messages.append({"op": "agent.control", "action": "stop"})
            thread.join(timeout=2)
        return client, snapshot

    def test_room_wake_forwards_one_activity_plugin_batch_before_declining(self) -> None:
        client, snapshot = self._run(fail=False)

        self.assertEqual(
            client.plugin_commands,
            [
                {
                    "plugin_id": "rimworld",
                    "action": "agent_turn",
                    "args": {
                        "colonist_id": "c2",
                        "act": {"action": "eat", "action_args": {}},
                        "speak": "식량부터 확인하겠습니다.",
                    },
                    "revision": str(snapshot["revision"]),
                    "request_id": "bridge-plugin-wake-plugin",
                }
            ],
        )

    def test_room_wake_records_provider_failure_on_the_assigned_colonist(self) -> None:
        client, _snapshot = self._run(fail=True)

        self.assertEqual(len(client.plugin_commands), 1)
        command = client.plugin_commands[0]
        self.assertEqual(command["action"], "model_error")
        self.assertEqual(command["args"]["colonist_id"], "c2")
        self.assertIn("RuntimeError", command["args"]["message"])

    def test_room_wake_reports_rejected_plugin_action_as_turn_failure(self) -> None:
        client, _snapshot = self._run(fail=False, reject_plugin=True)

        failed = next(
            payload
            for action, payload, _request_id in client.commands
            if action == "turn.failed"
        )
        self.assertEqual(failed["error_code"], "command_failed")
        self.assertIn("Invalid colony action", failed["message"])


if __name__ == "__main__":
    unittest.main()
