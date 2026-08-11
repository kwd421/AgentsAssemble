import io
import json
import unittest
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble.legacy.live_agent.http.presence import (
    register_legacy_live_agent_presence_routes,
)
from agentsassemble.web.router import GuiDeps, RequestContext, Router


class FakeHandler:
    def __init__(self, path: str, body: bytes) -> None:
        self.path = path
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.sent_json: dict[str, object] | None = None
        self.sent_error: tuple[HTTPStatus, str] | None = None

    def _send_json(self, payload: dict[str, object]) -> None:
        self.sent_json = payload

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self.sent_error = (status, message)


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.invalid_json: list[tuple[str, str]] = []
        self.failure: ValueError | None = None

    def register(self, payload: dict[str, object]) -> dict[str, object]:
        return self._run("register", "", payload)

    def heartbeat(self, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
        return self._run("heartbeat", agent_id, payload)

    def leave(self, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
        return self._run("leave", agent_id, payload)

    def record_invalid_json(self, operation: str, *, agent_id: str = "") -> None:
        self.invalid_json.append((operation, agent_id))

    def _run(self, action: str, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((action, agent_id, payload))
        if self.failure is not None:
            raise self.failure
        return {"status": "ok", "action": action, "agent_id": agent_id}


class LegacyLiveAgentPresenceRouteTests(unittest.TestCase):

    def setUp(self) -> None:
        self.service = FakeService()
        self.router = Router()
        register_legacy_live_agent_presence_routes(self.router, service=self.service)

    def dispatch(self, path: str, body: bytes) -> FakeHandler:
        handler = FakeHandler(path, body)
        parsed = urlparse(path)
        context = RequestContext(handler, GuiDeps(output_root=Path(".")), parsed, parse_qs(parsed.query))
        self.assertTrue(self.router.dispatch("POST", context))
        return handler

    def test_routes_delegate_to_presence_service(self) -> None:
        register_payload = {"agent_id": "agent-a"}
        heartbeat_payload = {"status": "error"}
        leave_payload = {"last_observed_event_id": "event-2"}

        registered = self.dispatch("/api/live-agents", json.dumps(register_payload).encode())
        heartbeat = self.dispatch(
            "/api/live-agents/agent-a/heartbeat",
            json.dumps(heartbeat_payload).encode(),
        )
        leave = self.dispatch(
            "/api/live-agents/agent-a/leave",
            json.dumps(leave_payload).encode(),
        )

        self.assertEqual(registered.sent_json["action"], "register")
        self.assertEqual(heartbeat.sent_json["action"], "heartbeat")
        self.assertEqual(leave.sent_json["action"], "leave")
        self.assertEqual(
            self.service.calls,
            [
                ("register", "", register_payload),
                ("heartbeat", "agent-a", heartbeat_payload),
                ("leave", "agent-a", leave_payload),
            ],
        )

    def test_invalid_json_preserves_presence_audit_policy(self) -> None:
        self.dispatch("/api/live-agents", b"{bad")
        self.dispatch("/api/live-agents/agent-a/heartbeat", b"{bad")
        self.dispatch("/api/live-agents/agent-a/leave", b"{bad")

        self.assertEqual(
            self.service.invalid_json,
            [
                ("live_agent.register", ""),
                ("live_agent.leave", "agent-a"),
            ],
        )

    def test_domain_error_keeps_bad_request_contract(self) -> None:
        self.service.failure = ValueError("agent unavailable")
        response = self.dispatch("/api/live-agents/agent-a/heartbeat", b"{}")

        self.assertEqual(response.sent_error, (HTTPStatus.BAD_REQUEST, "agent unavailable"))


if __name__ == "__main__":
    unittest.main()
