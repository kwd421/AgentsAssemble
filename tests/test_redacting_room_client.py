import threading
import unittest

from agentsassemble.providers.agent_bridge import RoomAgentBridge
from agentsassemble.providers.redacting_room_client import CredentialRedactingRoomClient


class BlockingReceiveClient:
    """Behaves like the real WebSocket client while no room frame arrives."""

    def __init__(self):
        self.closed = False
        self.commands = []
        self.messages = []
        self.idle_receive_started = threading.Event()
        self.release_receive = threading.Event()
        self.receive_timeout_seconds = None
        self._lock = threading.Lock()

    def set_receive_timeout(self, seconds):
        self.receive_timeout_seconds = float(seconds)

    def receive(self):
        with self._lock:
            if self.messages:
                messages = list(self.messages)
                self.messages.clear()
                return messages
        self.idle_receive_started.set()
        self.release_receive.wait(self.receive_timeout_seconds)
        with self._lock:
            messages = list(self.messages)
            self.messages.clear()
        return messages

    def command(self, action, payload=None, *, request_id=""):
        with self._lock:
            self.commands.append((action, dict(payload or {}), request_id))
            self.messages.append(
                {"op": "ack", "request_id": request_id, "accepted": True}
            )
        return request_id

    def close(self):
        self.closed = True
        self.release_receive.set()


class IdleRuntime:
    def __init__(self):
        self.running = False

    def start(self):
        self.running = True
        return self.health()

    def health(self):
        return {
            "pid": 4242,
            "running": self.running,
            "pty": False,
            "transport": "websocket",
            "provider_session_active": True,
            "is_one_shot": False,
            "resolved_executable": "/fake/provider",
            "started_at": "2026-01-01T00:00:00+00:00",
        }

    def set_request_handler(self, handler):
        self.request_handler = handler

    def stop(self, *, timeout_seconds=2.0):
        del timeout_seconds
        self.running = False


class CredentialRedactingRoomClientTests(unittest.TestCase):
    def test_wrapper_preserves_bounded_receive_for_prompt_bridge_stop(self):
        raw_client = BlockingReceiveClient()
        client = CredentialRedactingRoomClient(
            raw_client,
            sensitive_values=("runtime-credential-with-unknown-prefix-918273",),
        )
        bridge = RoomAgentBridge(
            client,
            IdleRuntime(),
            room_id="general",
            participant_id="deepseek",
            session_id="deepseek",
            receive_sleep_seconds=0.005,
            observed_checkpoint_interval_seconds=0.05,
        )
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        self.assertTrue(raw_client.idle_receive_started.wait(1.0))

        try:
            bridge.stop()
            thread.join(timeout=0.5)
            self.assertFalse(
                thread.is_alive(),
                "a locally stopped bridge must not remain blocked in WebSocket receive",
            )
        finally:
            raw_client.release_receive.set()
            thread.join(timeout=1.0)


if __name__ == "__main__":
    unittest.main()
