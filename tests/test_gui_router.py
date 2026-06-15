import io
import json
import unittest
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble import room_invite
from agentsassemble.gui_router import GuiDeps, RequestContext, Router


class FakeHandler:
    """Just enough of BaseHTTPRequestHandler for RequestContext."""

    def __init__(self, *, headers=None, body=b""):
        self.headers = dict(headers or {})
        self.rfile = io.BytesIO(body)
        if body and "Content-Length" not in self.headers:
            self.headers["Content-Length"] = str(len(body))
        self.sent_json = None
        self.sent_error = None

    def _send_json(self, payload):
        self.sent_json = payload

    def _send_error(self, status, message):
        self.sent_error = (status, message)


def _context(handler, path="/api/test"):
    parsed = urlparse(path)
    deps = GuiDeps(output_root=Path("."))
    return RequestContext(handler, deps, parsed, parse_qs(parsed.query))


class RouterDispatchTests(unittest.TestCase):
    def test_dispatch_runs_registered_handler_and_reports_handled(self):
        router = Router()
        seen = []

        @router.get("/api/ping")
        def ping(ctx):
            seen.append(ctx.path)
            ctx.send_json({"ok": True})

        handler = FakeHandler()
        self.assertTrue(router.dispatch("GET", _context(handler, "/api/ping?x=1")))
        self.assertEqual(seen, ["/api/ping"])
        self.assertEqual(handler.sent_json, {"ok": True})

    def test_dispatch_returns_false_for_unknown_route_and_wrong_method(self):
        router = Router()

        @router.post("/api/ping")
        def ping(ctx):
            ctx.send_json({})

        self.assertFalse(router.dispatch("GET", _context(FakeHandler(), "/api/ping")))
        self.assertFalse(router.dispatch("POST", _context(FakeHandler(), "/api/other")))

    def test_duplicate_registration_is_rejected(self):
        router = Router()
        router.add("GET", "/api/dup", lambda ctx: None)
        with self.assertRaises(ValueError):
            router.add("GET", "/api/dup", lambda ctx: None)


class RequestContextBodyTests(unittest.TestCase):
    def test_read_json_body_parses_dict(self):
        handler = FakeHandler(body=json.dumps({"a": 1}).encode())
        self.assertEqual(_context(handler).read_json_body(), {"a": 1})
        self.assertIsNone(handler.sent_error)

    def test_read_json_body_rejects_malformed_and_non_dict(self):
        for body in (b"{nope", b"[1, 2]"):
            handler = FakeHandler(body=body)
            self.assertIsNone(_context(handler).read_json_body())
            self.assertEqual(handler.sent_error[0], HTTPStatus.BAD_REQUEST)

    def test_read_json_body_empty_is_empty_dict(self):
        self.assertEqual(_context(FakeHandler()).read_json_body(), {})


class RequestContextIdentityTests(unittest.TestCase):
    def setUp(self):
        room_invite.reset_state()
        self.addCleanup(room_invite.reset_state)

    def test_require_host_rejects_without_token_when_gate_configured(self):
        room_invite.set_runtime_host_token("host-secret")
        handler = FakeHandler()
        self.assertFalse(_context(handler).require_host())
        self.assertEqual(handler.sent_error[0], HTTPStatus.FORBIDDEN)

    def test_require_host_accepts_header_and_bearer_token(self):
        room_invite.set_runtime_host_token("host-secret")
        for headers in (
            {"X-Host-Token": "host-secret"},
            {"Authorization": "Bearer host-secret"},
        ):
            handler = FakeHandler(headers=headers)
            self.assertTrue(_context(handler).require_host())
            self.assertIsNone(handler.sent_error)

    def test_require_session_rejects_missing_and_invalid_tokens(self):
        handler = FakeHandler()
        self.assertIsNone(_context(handler).require_session())
        self.assertEqual(handler.sent_error[0], HTTPStatus.UNAUTHORIZED)

        handler = FakeHandler(headers={"Authorization": "Bearer aas1.bogus"})
        self.assertIsNone(_context(handler).require_session())
        self.assertEqual(handler.sent_error[0], HTTPStatus.UNAUTHORIZED)


if __name__ == "__main__":
    unittest.main()
