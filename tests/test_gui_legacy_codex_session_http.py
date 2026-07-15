import io
import unittest
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble.gui_legacy_codex_session_http import (
    LegacyCodexSessionHttpDeps,
    register_legacy_codex_session_routes,
)
from agentsassemble.gui_router import GuiDeps, RequestContext, Router
from agentsassemble.legacy_codex_session_compat import LegacyCodexSessionError


class FakeHandler:
    def __init__(self, path: str, body: bytes) -> None:
        self.path = path
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


class FakeSessions:
    def __init__(self) -> None:
        self.invite_calls: list[dict[str, object]] = []
        self.join_calls: list[tuple[dict[str, object], str]] = []
        self.invite_error: LegacyCodexSessionError | None = None
        self.join_error: LegacyCodexSessionError | None = None

    def invite(self, payload: dict[str, object]) -> dict[str, object]:
        self.invite_calls.append(payload)
        if self.invite_error is not None:
            raise self.invite_error
        return {"binding": {"role_id": payload.get("role_id")}}

    def join(self, payload: dict[str, object], *, default_server: str) -> dict[str, object]:
        self.join_calls.append((payload, default_server))
        if self.join_error is not None:
            raise self.join_error
        return {"status": "ready", "meeting_id": payload.get("meeting_id")}


class LegacyCodexSessionRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sessions = FakeSessions()
        self.operation_payload: dict[str, object] | None = {"meeting_id": "m1"}
        self.operation_names: list[str] = []
        self.router = Router()
        register_legacy_codex_session_routes(
            self.router,
            deps=LegacyCodexSessionHttpDeps(
                sessions=self.sessions,
                read_operation_payload=self._read_operation_payload,
                request_server_url=lambda _ctx: "http://room.local:8765",
            ),
        )

    def dispatch(self, path: str, body: bytes = b"") -> FakeHandler:
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

    def _read_operation_payload(
        self,
        _ctx: RequestContext,
        operation: str,
    ) -> dict[str, object] | None:
        self.operation_names.append(operation)
        return self.operation_payload

    def test_invite_delegates_parsed_payload(self) -> None:
        response = self.dispatch(
            "/api/codex-sessions/invite",
            b'{"meeting_id":"m1","role_id":"critic","session_id":"session-a"}',
        )

        self.assertEqual(
            self.sessions.invite_calls,
            [{"meeting_id": "m1", "role_id": "critic", "session_id": "session-a"}],
        )
        self.assertEqual(response.sent_json, {"binding": {"role_id": "critic"}})

    def test_invite_invalid_json_does_not_reach_service(self) -> None:
        response = self.dispatch("/api/codex-sessions/invite", b"{bad")

        self.assertEqual(response.sent_error, (HTTPStatus.BAD_REQUEST, "Invalid JSON", None))
        self.assertEqual(self.sessions.invite_calls, [])

    def test_invite_safe_error_details_are_forwarded(self) -> None:
        self.sessions.invite_error = LegacyCodexSessionError(
            "Codex live session invite failed.",
            details={"role_id": "critic"},
        )

        response = self.dispatch("/api/codex-sessions/invite", b"{}")

        self.assertEqual(
            response.sent_error,
            (
                HTTPStatus.BAD_REQUEST,
                "Codex live session invite failed.",
                {"role_id": "critic"},
            ),
        )

    def test_join_uses_operation_reader_and_request_server(self) -> None:
        response = self.dispatch("/api/codex-sessions/join")

        self.assertEqual(self.operation_names, ["codex_session.join"])
        self.assertEqual(
            self.sessions.join_calls,
            [({"meeting_id": "m1"}, "http://room.local:8765")],
        )
        self.assertEqual(response.sent_json, {"status": "ready", "meeting_id": "m1"})

    def test_join_stops_when_operation_reader_rejects_body(self) -> None:
        self.operation_payload = None

        response = self.dispatch("/api/codex-sessions/join")

        self.assertEqual(self.sessions.join_calls, [])
        self.assertIsNone(response.sent_json)
        self.assertIsNone(response.sent_error)

    def test_join_safe_error_details_are_forwarded(self) -> None:
        self.sessions.join_error = LegacyCodexSessionError(
            "Codex live session join failed.",
            details={"meeting_id": "m1", "role_id": "critic"},
        )

        response = self.dispatch("/api/codex-sessions/join")

        self.assertEqual(
            response.sent_error,
            (
                HTTPStatus.BAD_REQUEST,
                "Codex live session join failed.",
                {"meeting_id": "m1", "role_id": "critic"},
            ),
        )


if __name__ == "__main__":
    unittest.main()
