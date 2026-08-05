from __future__ import annotations

import threading
import time
import unittest

from agentsassemble.providers.agent_bridge import RoomAgentBridge
from tests.test_room_agent_bridge import FakeClient, FakeRuntime, _wait_for


class RoomAgentBridgeProviderRequestTests(unittest.TestCase):
    def test_stale_provider_resolution_does_not_close_the_bridge(self) -> None:
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
                "op": "provider.request.resolve",
                "provider_request_id": "request-from-a-previous-bridge-lease",
                "option_id": "allow-once",
            }
        )
        time.sleep(0.05)

        self.assertTrue(thread.is_alive())
        self.assertFalse(client.closed)

        client.messages.append({"op": "agent.control", "action": "stop"})
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
