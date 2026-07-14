import io
import json
import unittest
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble.gui_legacy_review_checkpoint_http import register_legacy_review_checkpoint_route
from agentsassemble.gui_router import GuiDeps, RequestContext, Router


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
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.invalid_json_count = 0
        self.failure: ValueError | None = None

    def create(self, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((meeting_id, payload))
        if self.failure is not None:
            raise self.failure
        return {"status": "answered", "meeting_id": meeting_id}

    def record_invalid_json(self) -> None:
        self.invalid_json_count += 1


class LegacyReviewCheckpointRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = FakeService()
        self.router = Router()
        register_legacy_review_checkpoint_route(self.router, service=self.service)

    def dispatch(self, body: bytes) -> FakeHandler:
        handler = FakeHandler("/api/meetings/room-a/review-checkpoints", body)
        parsed = urlparse(handler.path)
        context = RequestContext(handler, GuiDeps(output_root=Path(".")), parsed, parse_qs(parsed.query))
        self.assertTrue(self.router.dispatch("POST", context))
        return handler

    def test_create_delegates_to_review_service(self) -> None:
        response = self.dispatch(json.dumps({"group_id": "resident-main"}).encode())

        self.assertEqual(response.sent_json, {"status": "answered", "meeting_id": "room-a"})
        self.assertEqual(self.service.calls, [("room-a", {"group_id": "resident-main"})])

    def test_invalid_json_records_failed_operation(self) -> None:
        response = self.dispatch(b"{bad")

        self.assertEqual(response.sent_error, (HTTPStatus.BAD_REQUEST, "Invalid JSON"))
        self.assertEqual(self.service.invalid_json_count, 1)

    def test_domain_error_keeps_bad_request_contract(self) -> None:
        self.service.failure = ValueError("session not ready")
        response = self.dispatch(b"{}")

        self.assertEqual(response.sent_error, (HTTPStatus.BAD_REQUEST, "session not ready"))


if __name__ == "__main__":
    unittest.main()
