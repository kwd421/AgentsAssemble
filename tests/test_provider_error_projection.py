from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.room.realtime import RoomRealtimeController
from tests.room_realtime_test_support import memory_room_access_services
from tests.test_room_realtime import (
    FakeBridgeManager,
    HOST,
    _bridge_identity,
    _external_ready_payload,
    _spec,
    _test_provider_catalog,
)


class ProviderErrorProjectionTests(unittest.TestCase):
    def test_quota_failure_survives_the_bridge_room_and_public_session_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = RoomRealtimeController(
                root,
                **memory_room_access_services().controller_kwargs(),
                providers=[_spec()],
                bridge_manager=FakeBridgeManager(),
                provider_catalog=_test_provider_catalog(),
            )
            try:
                controller.handle_command(
                    HOST,
                    {
                        "op": "command",
                        "request_id": "start-provider",
                        "action": "agent.start",
                        "payload": {"agent_id": "codex"},
                    },
                )
                bridge_identity = _bridge_identity()
                controller.store.update_session_fields(
                    "general",
                    "codex",
                    bridge_handle_id="handle-codex",
                )
                bridge_channel = controller.connect(bridge_identity)
                bridge_channel.subscribe({"room_events"})
                controller.handle_command(
                    bridge_identity,
                    {
                        "op": "command",
                        "request_id": "provider-ready",
                        "action": "bridge.ready",
                        "payload": _external_ready_payload(),
                    },
                )
                controller.handle_command(
                    HOST,
                    {
                        "op": "command",
                        "request_id": "send-provider-work",
                        "action": "message.send",
                        "payload": {
                            "content": "check the remaining quota",
                            "target_agent_id": "codex",
                        },
                    },
                )
                assignment = next(
                    message
                    for message in bridge_channel.drain()
                    if message.get("op") in {"room.wake", "turn.assign"}
                )

                result = controller.handle_command(
                    bridge_identity,
                    {
                        "op": "command",
                        "request_id": "report-quota-failure",
                        "action": "turn.failed",
                        "payload": {
                            "turn_id": assignment["turn_id"],
                            "message": "Provider usage quota is exhausted.",
                            "error_code": "quota_exhausted",
                        },
                    },
                )["result"]

                stored = controller.store.session("general", "codex")
                public_session = next(
                    session
                    for session in controller.snapshot(HOST)["agent_sessions"]
                    if session["session_id"] == "codex"
                )
                self.assertEqual(stored["last_error_code"], "quota_exhausted")
                self.assertEqual(result["agent_session"]["last_error_code"], "quota_exhausted")
                self.assertEqual(public_session["last_error_code"], "quota_exhausted")
                self.assertEqual(result["event"]["error_code"], "quota_exhausted")
            finally:
                controller.close()


if __name__ == "__main__":
    unittest.main()
