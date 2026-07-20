import unittest
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble.gui_legacy_live_agent_official_reply_http import (
    register_legacy_live_agent_official_reply_route as compatibility_register,
)
from agentsassemble.legacy.live_agent.http.official_reply import (
    LegacyLiveAgentOfficialReplyHttpDeps,
    register_legacy_live_agent_official_reply_route,
)
from agentsassemble.web.router import GuiDeps, RequestContext, Router


class FakeHandler:
    def __init__(self) -> None:
        self.path = "/api/live-agents/agent-a/official-turn"
        self.headers: dict[str, str] = {}
        self.sent_json: dict[str, object] | None = None
        self.sent_error: tuple[HTTPStatus, str] | None = None

    def _send_json(self, payload: dict[str, object]) -> None:
        self.sent_json = payload

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self.sent_error = (status, message)


class FakeReplyService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.failure: ValueError | None = None

    def reply(self, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((agent_id, payload))
        if self.failure is not None:
            raise self.failure
        return {"event": {"actor_id": agent_id}}


class LegacyLiveAgentOfficialReplyRouteTests(unittest.TestCase):
    def test_root_module_exports_owned_registrar(self) -> None:
        self.assertIs(compatibility_register, register_legacy_live_agent_official_reply_route)

    def setUp(self) -> None:
        self.service = FakeReplyService()
        self.payload: dict[str, object] | None = {"source_event_id": "source-a"}
        self.read_calls: list[tuple[str, str]] = []
        self.router = Router()
        register_legacy_live_agent_official_reply_route(
            self.router,
            deps=LegacyLiveAgentOfficialReplyHttpDeps(
                replies=self.service,
                read_operation_payload=self.read_payload,
            ),
        )

    def read_payload(
        self,
        ctx: RequestContext,
        operation: str,
        target_id: str,
    ) -> dict[str, object] | None:
        self.read_calls.append((operation, target_id))
        return self.payload

    def dispatch(self) -> FakeHandler:
        handler = FakeHandler()
        parsed = urlparse(handler.path)
        context = RequestContext(
            handler,
            GuiDeps(output_root=Path(".")),
            parsed,
            parse_qs(parsed.query),
        )
        self.assertTrue(self.router.dispatch("POST", context))
        return handler

    def test_route_delegates_with_operation_identity(self) -> None:
        response = self.dispatch()

        self.assertEqual(self.read_calls, [("official_turn.reply", "agent-a")])
        self.assertEqual(
            self.service.calls,
            [("agent-a", {"source_event_id": "source-a"})],
        )
        self.assertEqual(response.sent_json, {"event": {"actor_id": "agent-a"}})

    def test_invalid_payload_stops_before_service(self) -> None:
        self.payload = None
        response = self.dispatch()

        self.assertEqual(self.service.calls, [])
        self.assertIsNone(response.sent_json)
        self.assertIsNone(response.sent_error)

    def test_domain_failure_keeps_bad_request_contract(self) -> None:
        self.service.failure = ValueError("Matching official turn request was not found.")
        response = self.dispatch()

        self.assertEqual(
            response.sent_error,
            (HTTPStatus.BAD_REQUEST, "Matching official turn request was not found."),
        )


if __name__ == "__main__":
    unittest.main()
