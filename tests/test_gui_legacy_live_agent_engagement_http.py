import io
import unittest
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble.gui_legacy_live_agent_engagement_http import (
    register_legacy_live_agent_engagement_route as compatibility_register,
)
from agentsassemble.legacy.live_agent.http.engagement import (
    register_legacy_live_agent_engagement_route,
)
from agentsassemble.web.router import GuiDeps, RequestContext, Router


class FakeHandler:
    def __init__(self, body: bytes) -> None:
        self.path = "/api/live-agents/agent-a/engagement"
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
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.invalid_agent_ids: list[str] = []
        self.failure: ValueError | None = None

    def update(self, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((agent_id, payload))
        if self.failure is not None:
            raise self.failure
        return {"agent": {"agent_id": agent_id, **payload}}

    def record_invalid_json(self, agent_id: str) -> None:
        self.invalid_agent_ids.append(agent_id)


class LegacyLiveAgentEngagementRouteTests(unittest.TestCase):
    def test_root_module_exports_owned_registrar(self) -> None:
        self.assertIs(compatibility_register, register_legacy_live_agent_engagement_route)

    def setUp(self) -> None:
        self.service = FakeService()
        self.router = Router()
        register_legacy_live_agent_engagement_route(self.router, service=self.service)

    def dispatch(self, body: bytes) -> FakeHandler:
        handler = FakeHandler(body)
        parsed = urlparse(handler.path)
        context = RequestContext(handler, GuiDeps(output_root=Path(".")), parsed, parse_qs(parsed.query))
        self.assertTrue(self.router.dispatch("POST", context))
        return handler

    def test_route_delegates_update(self) -> None:
        response = self.dispatch(b'{"engagement_mode":"watch"}')

        self.assertEqual(self.service.calls, [("agent-a", {"engagement_mode": "watch"})])
        self.assertEqual(response.sent_json["agent"]["engagement_mode"], "watch")

    def test_invalid_json_records_failed_operation(self) -> None:
        response = self.dispatch(b"{bad")

        self.assertEqual(response.sent_error, (HTTPStatus.BAD_REQUEST, "Invalid JSON"))
        self.assertEqual(self.service.invalid_agent_ids, ["agent-a"])

    def test_domain_error_keeps_bad_request_contract(self) -> None:
        self.service.failure = ValueError("invalid engagement mode")
        response = self.dispatch(b"{}")

        self.assertEqual(response.sent_error, (HTTPStatus.BAD_REQUEST, "invalid engagement mode"))


if __name__ == "__main__":
    unittest.main()
