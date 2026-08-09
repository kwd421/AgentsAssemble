from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from agentsassemble.providers.agent_bridge import RoomAgentBridge
from agentsassemble.providers.api_session import ApiContextCheckpointMissing
from agentsassemble.providers.room_portal import RoomPortal
from agentsassemble.room.realtime import NativeCliProviderSpec, RoomRealtimeController
from tests.test_room_agent_bridge import FakeClient, FakeRuntime, _wait_for
from tests.room_realtime_test_support import FakeBridgeManager, memory_room_access_services


class RoomAgentBridgeFailureTests(unittest.TestCase):
    def test_start_failure_crosses_the_realtime_boundary_into_durable_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            room_access = memory_room_access_services()
            controller = RoomRealtimeController(
                Path(temp_dir),
                **room_access.controller_kwargs(),
                providers=[
                    NativeCliProviderSpec(
                        agent_id="deepseek",
                        display_name="DeepSeek",
                        command=("deepseek",),
                        cwd=".",
                    )
                ],
                bridge_manager=FakeBridgeManager(),
            )
            identity = {
                "agent_id": "deepseek",
                "display_name": "DeepSeek",
                "participant_type": "agent",
                "client_type": "agent_bridge",
                "invite_scope": "read_write",
                "meeting_id": "general",
                "session_id": "deepseek",
                "operator": False,
            }
            controller.connect(identity)
            try:
                response = controller.handle_command(
                    identity,
                    {
                        "op": "command",
                        "request_id": "startup-failed",
                        "action": "bridge.start_failed",
                        "payload": {
                            "error_code": "api_context_checkpoint_missing",
                            "message": "Prior API turns have no recoverable checkpoint.",
                        },
                    },
                )
                session = controller.store.session("general", "deepseek")
                events = controller.store.read_events("general")
            finally:
                controller.close()

        self.assertTrue(response["accepted"])
        self.assertEqual(session["last_error_code"], "api_context_checkpoint_missing")
        self.assertEqual(session["runtime_status"], "error")
        self.assertTrue(
            any(
                event.get("type") == "error"
                and event.get("error_code") == "api_context_checkpoint_missing"
                for event in events
            )
        )

    def test_runtime_start_failure_is_reported_before_the_bridge_exits(self) -> None:
        class MissingCheckpointRuntime(FakeRuntime):
            def start(self):
                raise ApiContextCheckpointMissing(
                    "This API Agent Session has prior turns but no recoverable checkpoint."
                )

        client = FakeClient()
        bridge = RoomAgentBridge(
            client,
            MissingCheckpointRuntime(),
            room_id="general",
            participant_id="deepseek",
            session_id="deepseek",
            receive_sleep_seconds=0.005,
        )

        exit_code = bridge.run()

        failure = next(
            payload
            for action, payload, _ in client.commands
            if action == "bridge.start_failed"
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(failure["error_code"], "api_context_checkpoint_missing")
        self.assertIn("no recoverable checkpoint", failure["message"])
        self.assertFalse(bridge.reconnect_permitted)

    def test_room_wake_without_a_read_receipt_fails_once_instead_of_declining(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="solar")
            portal.prepare()
            client = FakeClient()
            bridge = RoomAgentBridge(
                client,
                FakeRuntime(),
                room_id="general",
                participant_id="solar",
                session_id="solar",
                receive_sleep_seconds=0.005,
                room_portal=portal,
            )
            thread = threading.Thread(target=bridge.run, daemon=True)
            thread.start()
            _wait_for(
                lambda: any(
                    action == "bridge.ready" for action, _, _ in client.commands
                )
            )
            with client._lock:
                client.messages.append(
                    {
                        "op": "room.wake",
                        "room_id": "general",
                        "participant_id": "solar",
                        "session_id": "solar",
                        "turn_id": "wake-unread",
                        "input_up_to_seq": 1,
                        "attachment_ids": [],
                        "observation_kind": "ambient_observation",
                        "publication_mode": "explicit_room_portal",
                        "timeout_seconds": 2,
                    }
                )
            _wait_for(
                lambda: any(
                    action == "turn.failed" for action, _, _ in client.commands
                )
            )
            with client._lock:
                client.messages.append({"op": "agent.control", "action": "stop"})
            thread.join(timeout=2)

        failure = next(
            payload
            for action, payload, _ in client.commands
            if action == "turn.failed"
        )
        self.assertEqual(failure["error_code"], "room_observation_unconfirmed")
        self.assertIn("did not read", failure["message"])
        self.assertFalse(
            any(action == "turn.decline" for action, _, _ in client.commands)
        )


if __name__ == "__main__":
    unittest.main()
