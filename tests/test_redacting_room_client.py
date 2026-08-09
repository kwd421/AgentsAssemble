import threading
import time
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


class SplitCredentialRuntime(IdleRuntime):
    def __init__(self, credential: str) -> None:
        super().__init__()
        self.credential = credential

    def send(self, text: str) -> None:
        self.input = text

    def read_output(self, *, timeout_seconds: float, on_delta=None, on_activity=None):
        del timeout_seconds, on_activity
        split_at = len(self.credential) // 2
        on_delta(f"prefix {self.credential[:split_at]}")
        on_delta(f"{self.credential[split_at:]} suffix")
        return {
            "outcome": "message",
            "content": f"prefix {self.credential} suffix",
            "metadata": {},
        }

    def interrupt(self) -> None:
        return None


def wait_for(predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met")


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
            receive_timeout_seconds=0.05,
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

    def test_split_provider_credential_cannot_reassemble_from_public_message_deltas(self):
        credential = "runtime-credential-with-unknown-prefix-918273"
        raw_client = BlockingReceiveClient()
        client = CredentialRedactingRoomClient(
            raw_client,
            sensitive_values=(credential,),
        )
        bridge = RoomAgentBridge(
            client,
            SplitCredentialRuntime(credential),
            room_id="general",
            participant_id="deepseek",
            session_id="deepseek",
            receive_sleep_seconds=0.005,
        )
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        wait_for(
            lambda: any(
                action == "bridge.ready" for action, _, _ in raw_client.commands
            )
        )
        with raw_client._lock:
            raw_client.messages.append(
                {
                    "op": "turn.assign",
                    "room_id": "general",
                    "participant_id": "deepseek",
                    "session_id": "deepseek",
                    "turn_id": "turn-split-secret",
                    "provider_input": "respond",
                    "publication_mode": "automatic_final",
                    "timeout_seconds": 2,
                }
            )
        wait_for(
            lambda: any(
                action == "message.final" for action, _, _ in raw_client.commands
            )
        )
        with raw_client._lock:
            raw_client.messages.append({"op": "agent.control", "action": "stop"})
        thread.join(timeout=2.0)

        public_delta_text = "".join(
            str(payload.get("content") or "")
            for action, payload, _ in raw_client.commands
            if action == "message.delta"
        )
        final_payload = next(
            payload
            for action, payload, _ in raw_client.commands
            if action == "message.final"
        )
        self.assertFalse(thread.is_alive())
        self.assertNotIn(credential, public_delta_text)
        self.assertNotIn(credential, str(final_payload.get("content") or ""))
        self.assertIn("[redacted]", str(final_payload.get("content") or ""))


if __name__ == "__main__":
    unittest.main()
