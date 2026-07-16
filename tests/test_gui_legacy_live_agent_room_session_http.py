import io
import json
import unittest
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble.gui_legacy_live_agent_room_session_http import register_legacy_room_session_route
from agentsassemble.web.router import GuiDeps, RequestContext, Router


class FakeHandler:
    def __init__(self, body: bytes) -> None:
        self.path = "/api/live-agent-room/delete-session"
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.sent_json: dict[str, object] | None = None
        self.sent_error: tuple[HTTPStatus, str, dict[str, object] | None] | None = None

    def _send_json(self, payload: dict[str, object]) -> None:
        self.sent_json = payload

    def _send_error(
        self,
        status: HTTPStatus,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        self.sent_error = (status, message, details)


class FakeService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.invalid_json_count = 0
        self.failure: ValueError | None = None

    def delete(self, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(payload)
        if self.failure is not None:
            raise self.failure
        return {"status": "deleted", "agent_id": str(payload.get("agent_id") or "")}

    def record_invalid_json(self) -> None:
        self.invalid_json_count += 1


class LegacyRoomSessionRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = FakeService()
        self.router = Router()
        register_legacy_room_session_route(self.router, service=self.service)

    def dispatch(self, body: bytes) -> FakeHandler:
        handler = FakeHandler(body)
        parsed = urlparse(handler.path)
        context = RequestContext(handler, GuiDeps(output_root=Path(".")), parsed, parse_qs(parsed.query))
        self.assertTrue(self.router.dispatch("POST", context))
        return handler

    def test_delete_delegates_and_returns_service_result(self) -> None:
        response = self.dispatch(json.dumps({"meeting_id": "room-a", "agent_id": "agent-a"}).encode())

        self.assertEqual(response.sent_json, {"status": "deleted", "agent_id": "agent-a"})
        self.assertEqual(self.service.calls, [{"meeting_id": "room-a", "agent_id": "agent-a"}])

    def test_invalid_json_and_delete_error_keep_http_contract(self) -> None:
        invalid = self.dispatch(b"{bad")
        self.service.failure = ValueError("session not owned")
        failed = self.dispatch(json.dumps({"meeting_id": "room-a", "agent_id": "agent-b"}).encode())

        self.assertEqual(invalid.sent_error, (HTTPStatus.BAD_REQUEST, "Invalid JSON", None))
        self.assertEqual(self.service.invalid_json_count, 1)
        self.assertEqual(
            failed.sent_error,
            (HTTPStatus.BAD_REQUEST, "session not owned", {"agent_id": "agent-b"}),
        )


if __name__ == "__main__":
    unittest.main()
