import json
import threading
import unittest
from unittest.mock import MagicMock, patch

from agentsassemble.application.room_attendee import AgentAttendee


class CredentialEchoingRuntime:
    def __init__(self, credential: str) -> None:
        self.credential = credential
        self.running = False

    def set_request_handler(self, handler) -> None:
        self.request_handler = handler

    def start(self) -> dict[str, object]:
        self.running = True
        return self.health()

    def health(self) -> dict[str, object]:
        return {
            "running": self.running,
            "transport": "https",
            "provider_session_active": self.running,
            "started_at": "2026-08-09T00:00:00+00:00",
            "last_error": f"provider echoed {self.credential}",
        }

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        del timeout_seconds
        self.running = False


class StopAfterReadyClient:
    def __init__(self) -> None:
        self.closed = False
        self.commands: list[tuple[str, dict[str, object], str]] = []
        self._messages: list[dict[str, object]] = []
        self._lock = threading.Lock()

    def set_receive_timeout(self, seconds: float) -> None:
        self.receive_timeout_seconds = seconds

    def command(
        self,
        action: str,
        payload: dict[str, object] | None = None,
        *,
        request_id: str = "",
    ) -> str:
        with self._lock:
            self.commands.append((action, dict(payload or {}), request_id))
            self._messages.append(
                {"op": "ack", "request_id": request_id, "accepted": True}
            )
            if action == "bridge.ready":
                self._messages.append({"op": "agent.control", "action": "stop"})
        return request_id

    def receive(self) -> list[dict[str, object]]:
        with self._lock:
            messages = list(self._messages)
            self._messages.clear()
        return messages

    def close(self) -> None:
        self.closed = True


class AgentAttendeeSecurityTests(unittest.TestCase):
    def test_external_attendee_redacts_its_provider_credential_before_room_reports(self) -> None:
        credential = "external-provider-credential-918273645"
        runtime = CredentialEchoingRuntime(credential)
        client = StopAfterReadyClient()
        attendee = AgentAttendee(
            invite_url="https://room.example/join?token=aai1.external-invite-918273",
            provider_id="deepseek",
        )
        leave_response = MagicMock()
        leave_response.__enter__.return_value.read.return_value = b""

        with (
            patch(
                "agentsassemble.application.room_attendee.join_agent_room_session",
                return_value={
                    "session_token": "external-session-token-918273645",
                    "agent_id": "deepseek-external",
                    "meeting_id": "general",
                    "provider_kind": "deepseek_api",
                    "guide": {},
                },
            ),
            patch(
                "agentsassemble.application.room_attendee.connect_room_ws",
                return_value=client,
            ),
            patch(
                "agentsassemble.application.room_attendee.PROVIDER_SECRETS.get",
                return_value=credential,
            ),
            patch(
                "agentsassemble.application.room_attendee.runtime_from_config",
                return_value=runtime,
            ),
            patch(
                "agentsassemble.application.room_attendee.urlopen",
                return_value=leave_response,
            ),
        ):
            self.assertEqual(attendee.run(), 0)

        transmitted = json.dumps(client.commands, ensure_ascii=False)
        self.assertNotIn(credential, transmitted)
        self.assertIn("[redacted]", transmitted)


if __name__ == "__main__":
    unittest.main()
