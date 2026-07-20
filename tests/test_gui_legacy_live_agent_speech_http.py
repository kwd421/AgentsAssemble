import io
import unittest
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble.gui_legacy_live_agent_speech_http import (
    register_legacy_live_agent_speech_routes as compatibility_register,
)
from agentsassemble.legacy.live_agent.http.speech import register_legacy_live_agent_speech_routes
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


class FakeSpeechService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.failure: ValueError | None = None

    def post_dm_reply(self, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
        return self._post("dm", agent_id, payload)

    def post_lobby_message(self, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
        return self._post("lobby", agent_id, payload)

    def _post(self, kind: str, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((kind, agent_id, payload))
        if self.failure is not None:
            raise self.failure
        return {"kind": kind, "agent_id": agent_id}


class LegacyLiveAgentSpeechRouteTests(unittest.TestCase):
    def test_root_module_exports_owned_registrar(self) -> None:
        self.assertIs(compatibility_register, register_legacy_live_agent_speech_routes)

    def setUp(self) -> None:
        self.service = FakeSpeechService()
        self.router = Router()
        register_legacy_live_agent_speech_routes(self.router, service=self.service)

    def dispatch(self, path: str, body: bytes) -> FakeHandler:
        handler = FakeHandler(path, body)
        parsed = urlparse(path)
        context = RequestContext(
            handler,
            GuiDeps(output_root=Path(".")),
            parsed,
            parse_qs(parsed.query),
        )
        self.assertTrue(self.router.dispatch("POST", context))
        return handler

    def test_lobby_route_delegates_to_service(self) -> None:
        response = self.dispatch(
            "/api/live-agents/agent-a/lobby",
            b'{"message":"hello"}',
        )

        self.assertEqual(self.service.calls, [("lobby", "agent-a", {"message": "hello"})])
        self.assertEqual(response.sent_json, {"kind": "lobby", "agent_id": "agent-a"})

    def test_dm_route_delegates_to_service(self) -> None:
        response = self.dispatch(
            "/api/live-agents/agent-a/dm-reply",
            b'{"message":"private"}',
        )

        self.assertEqual(self.service.calls, [("dm", "agent-a", {"message": "private"})])
        self.assertEqual(response.sent_json, {"kind": "dm", "agent_id": "agent-a"})

    def test_invalid_json_stops_before_service(self) -> None:
        response = self.dispatch("/api/live-agents/agent-a/lobby", b"{bad")

        self.assertEqual(self.service.calls, [])
        self.assertEqual(response.sent_error, (HTTPStatus.BAD_REQUEST, "Invalid JSON"))

    def test_domain_error_keeps_bad_request_contract(self) -> None:
        self.service.failure = ValueError("Message is required.")
        response = self.dispatch("/api/live-agents/agent-a/lobby", b"{}")

        self.assertEqual(response.sent_error, (HTTPStatus.BAD_REQUEST, "Message is required."))


if __name__ == "__main__":
    unittest.main()
