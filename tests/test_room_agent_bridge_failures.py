from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agentsassemble.application import agent_bridge_entrypoint
from agentsassemble.providers.agent_bridge import RoomAgentBridge
from agentsassemble.providers.api_session import ApiContextCheckpointMissing
from agentsassemble.providers.bridge_launch_secrets import encode_secure_launch_payload
from agentsassemble.providers.room_portal import RoomPortal
from agentsassemble.room.realtime import NativeCliProviderSpec, RoomRealtimeController
from tests.test_room_agent_bridge import FakeClient, FakeRuntime, _launch_config, _wait_for
from tests.room_realtime_test_support import FakeBridgeManager, memory_room_access_services


class RoomAgentBridgeFailureTests(unittest.TestCase):
    def test_entrypoint_reports_runtime_construction_failure_to_the_room(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "bridge.json"
            config_path.write_text(
                json.dumps(
                    _launch_config(
                        participant_id="tokenrouter",
                        session_id="tokenrouter",
                        provider_kind="tokenrouter_api",
                        runtime_kind="api",
                        command=["server-owned-api"],
                        model="moonshotai/kimi-k3-free",
                        reasoning_effort="",
                        service_tier="",
                        transport="https",
                        provider_endpoint="https://api.tokenrouter.com/v1",
                        runtime_state_dir=str(Path(temp_dir) / "provider-state"),
                        credential_stdin=True,
                        resume_required=True,
                    )
                ),
                encoding="utf-8",
            )
            client = FakeClient()
            launch_input = encode_secure_launch_payload(
                {
                    "credential": "test-tokenrouter-key",
                    "session_token": "renewable-session-token",
                }
            )
            environment = {
                "AGENTSASSEMBLE_BRIDGE_SERVER_URL": "ws://127.0.0.1:8765",
                "AGENTSASSEMBLE_BRIDGE_TICKET": "one-shot-ticket",
                "AGENTSASSEMBLE_BRIDGE_CONFIG": str(config_path),
            }
            with (
                patch.dict(os.environ, environment, clear=False),
                patch.object(
                    agent_bridge_entrypoint.sys,
                    "stdin",
                    SimpleNamespace(buffer=io.BytesIO(launch_input)),
                ),
                patch.object(
                    agent_bridge_entrypoint,
                    "connect_room_ws_with_ticket",
                    return_value=client,
                ),
            ):
                exit_code = agent_bridge_entrypoint.main()

        failure = next(
            payload
            for action, payload, _request_id in client.commands
            if action == "bridge.start_failed"
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(failure["error_code"], "api_context_checkpoint_missing")
        self.assertIn("no recoverable checkpoint", failure["message"])

    def test_entrypoint_bounds_ready_ack_timeout_reconnects_across_connections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "bridge.json"
            config_path.write_text(
                json.dumps(
                    _launch_config(
                        participant_id="tokenrouter",
                        session_id="tokenrouter",
                        provider_kind="tokenrouter_api",
                        runtime_kind="api",
                        command=["server-owned-api"],
                        model="moonshotai/kimi-k3-free",
                        reasoning_effort="",
                        service_tier="",
                        transport="https",
                        provider_endpoint="https://api.tokenrouter.com/v1",
                        runtime_state_dir=str(Path(temp_dir) / "provider-state"),
                        credential_stdin=True,
                    )
                ),
                encoding="utf-8",
            )
            clients = [FakeClient(), FakeClient()]
            for client in clients:
                client.command_responses["bridge.ready"] = None
            reconnect_calls = 0

            def reconnect(*_args, **_kwargs):
                nonlocal reconnect_calls
                reconnect_calls += 1
                if reconnect_calls > 1:
                    raise AssertionError("bridge ready ACK timeout retried without a process-wide bound")
                return clients[1]

            launch_input = encode_secure_launch_payload(
                {
                    "credential": "test-tokenrouter-key",
                    "session_token": "renewable-session-token",
                }
            )
            environment = {
                "AGENTSASSEMBLE_BRIDGE_SERVER_URL": "ws://127.0.0.1:8765",
                "AGENTSASSEMBLE_BRIDGE_TICKET": "one-shot-ticket",
                "AGENTSASSEMBLE_BRIDGE_CONFIG": str(config_path),
            }
            with (
                patch.dict(os.environ, environment, clear=False),
                patch.object(
                    agent_bridge_entrypoint.sys,
                    "stdin",
                    SimpleNamespace(buffer=io.BytesIO(launch_input)),
                ),
                patch.object(
                    agent_bridge_entrypoint,
                    "connect_room_ws_with_ticket",
                    return_value=clients[0],
                ),
                patch.object(
                    agent_bridge_entrypoint,
                    "connect_room_ws",
                    side_effect=reconnect,
                ),
            ):
                exit_code = agent_bridge_entrypoint.main()

        self.assertEqual(exit_code, 1)
        self.assertEqual(reconnect_calls, 1)
        self.assertEqual(
            sum(
                1
                for client in clients
                for action, _payload, _request_id in client.commands
                if action == "bridge.ready"
            ),
            2,
        )

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
            controller.store.update_session_fields(
                "general",
                "deepseek",
                runtime_status="starting",
            )
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

    def test_disconnected_bridge_cannot_record_a_start_failure(self) -> None:
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
            channel = controller.connect(identity)
            controller.disconnect(channel)
            try:
                with self.assertRaisesRegex(Exception, "no longer active") as raised:
                    controller.handle_command(
                        identity,
                        {
                            "op": "command",
                            "request_id": "disconnected-startup-failure",
                            "action": "bridge.start_failed",
                            "payload": {
                                "error_code": "provider_turn_failed",
                                "message": "late startup failure",
                            },
                        },
                    )
                session = controller.store.session("general", "deepseek")
            finally:
                controller.close()

        self.assertEqual(getattr(raised.exception, "code", ""), "bridge_disconnected")
        self.assertNotEqual(session["runtime_status"], "error")

    def test_stopped_session_cannot_be_recontaminated_by_a_late_start_failure(self) -> None:
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
            controller.store.update_session_fields(
                "general",
                "deepseek",
                status="detached",
                runtime_status="stopped",
                enabled=False,
            )
            try:
                with self.assertRaises(Exception) as raised:
                    controller.handle_command(
                        identity,
                        {
                            "op": "command",
                            "request_id": "late-startup-failure",
                            "action": "bridge.start_failed",
                            "payload": {
                                "error_code": "provider_turn_failed",
                                "message": "late startup failure",
                            },
                        },
                    )
                session = controller.store.session("general", "deepseek")
            finally:
                controller.close()

        self.assertEqual(getattr(raised.exception, "code", ""), "bridge_start_failure_stale")
        self.assertEqual(session["runtime_status"], "stopped")

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
                        "input_up_to_seq": 0,
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

    def test_room_wake_preserves_provider_failure_when_no_read_receipt_exists(self) -> None:
        class FailingRuntime(FakeRuntime):
            def read_output(self, *, timeout_seconds, on_delta=None, on_activity=None):
                del timeout_seconds, on_delta, on_activity
                raise RuntimeError("upstream provider failed before reading")

        with tempfile.TemporaryDirectory() as temp_dir:
            portal = RoomPortal(Path(temp_dir) / "portal", participant_id="solar")
            portal.prepare()
            client = FakeClient()
            bridge = RoomAgentBridge(
                client,
                FailingRuntime(),
                room_id="general",
                participant_id="solar",
                session_id="solar",
                receive_sleep_seconds=0.005,
                room_portal=portal,
            )
            thread = threading.Thread(target=bridge.run, daemon=True)
            thread.start()
            _wait_for(
                lambda: any(action == "bridge.ready" for action, _, _ in client.commands)
            )
            with client._lock:
                client.messages.append(
                    {
                        "op": "room.wake",
                        "room_id": "general",
                        "participant_id": "solar",
                        "session_id": "solar",
                        "turn_id": "wake-provider-failure",
                        "input_up_to_seq": 0,
                        "attachment_ids": [],
                        "observation_kind": "ambient_observation",
                        "publication_mode": "explicit_room_portal",
                        "timeout_seconds": 2,
                    }
                )
            _wait_for(
                lambda: any(action == "turn.failed" for action, _, _ in client.commands)
            )
            with client._lock:
                client.messages.append({"op": "agent.control", "action": "stop"})
            thread.join(timeout=2)

        failure = next(
            payload
            for action, payload, _ in client.commands
            if action == "turn.failed"
        )
        self.assertEqual(failure["error_code"], "provider_turn_failed")
        self.assertIn("upstream provider failed", failure["message"])


if __name__ == "__main__":
    unittest.main()
