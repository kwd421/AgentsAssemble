import io
import json
import unittest
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble.legacy.meeting.http.lifecycle import (
    register_legacy_meeting_lifecycle_routes,
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
        self.start_calls: list[dict[str, object]] = []
        self.finalize_calls: list[tuple[str, dict[str, object]]] = []
        self.invalid_json_calls: list[tuple[str, str]] = []
        self.start_failure: ValueError | None = None
        self.finalize_failure: ValueError | None = None

    def start(self, payload: dict[str, object]) -> dict[str, object]:
        self.start_calls.append(payload)
        if self.start_failure is not None:
            raise self.start_failure
        return {"status": "started", "meeting_id": str(payload.get("meeting_id") or "")}

    def finalize(self, meeting_id: str, payload: dict[str, object]) -> dict[str, object]:
        self.finalize_calls.append((meeting_id, payload))
        if self.finalize_failure is not None:
            raise self.finalize_failure
        return {"status": "finalized", "meeting_id": meeting_id}

    def record_invalid_json(self, operation: str, *, meeting_id: str = "") -> None:
        self.invalid_json_calls.append((operation, meeting_id))


class LegacyMeetingLifecycleRouteTests(unittest.TestCase):

    def setUp(self) -> None:
        self.service = FakeService()
        self.router = Router()
        register_legacy_meeting_lifecycle_routes(self.router, service=self.service)

    def dispatch(self, path: str, body: bytes) -> FakeHandler:
        handler = FakeHandler(path, body)
        parsed = urlparse(handler.path)
        context = RequestContext(handler, GuiDeps(output_root=Path(".")), parsed, parse_qs(parsed.query))
        self.assertTrue(self.router.dispatch("POST", context))
        return handler

    def test_start_and_finalize_delegate_to_lifecycle_service(self) -> None:
        started = self.dispatch(
            "/api/live-agent-meetings/start",
            json.dumps({"meeting_id": "room-a"}).encode(),
        )
        finalized = self.dispatch(
            "/api/meetings/room-a/finalize",
            json.dumps({"close_pending": True}).encode(),
        )

        self.assertEqual(started.sent_json, {"status": "started", "meeting_id": "room-a"})
        self.assertEqual(self.service.start_calls, [{"meeting_id": "room-a"}])
        self.assertEqual(finalized.sent_json, {"status": "finalized", "meeting_id": "room-a"})
        self.assertEqual(self.service.finalize_calls, [("room-a", {"close_pending": True})])

    def test_invalid_json_records_the_matching_operation(self) -> None:
        start_response = self.dispatch("/api/live-agent-meetings/start", b"{bad")
        finalize_response = self.dispatch("/api/meetings/room-a/finalize", b"{bad")

        self.assertEqual(start_response.sent_error, (HTTPStatus.BAD_REQUEST, "Invalid JSON"))
        self.assertEqual(finalize_response.sent_error, (HTTPStatus.BAD_REQUEST, "Invalid JSON"))
        self.assertEqual(
            self.service.invalid_json_calls,
            [("meeting.start", ""), ("meeting.finalize", "room-a")],
        )

    def test_domain_errors_keep_bad_request_contract(self) -> None:
        self.service.start_failure = ValueError("invalid meeting")
        start_response = self.dispatch("/api/live-agent-meetings/start", b"{}")
        self.service.finalize_failure = ValueError("meeting is still busy")
        finalize_response = self.dispatch("/api/meetings/room-a/finalize", b"{}")

        self.assertEqual(start_response.sent_error, (HTTPStatus.BAD_REQUEST, "invalid meeting"))
        self.assertEqual(finalize_response.sent_error, (HTTPStatus.BAD_REQUEST, "meeting is still busy"))


if __name__ == "__main__":
    unittest.main()
