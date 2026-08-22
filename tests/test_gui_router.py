import io
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble import room_invite
from agentsassemble.features.side_chat.routes import register_side_chat_routes
from agentsassemble.features.side_chat.service import SideChatStore
from agentsassemble.features.message_pins.routes import register_message_pin_routes
from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.web.router import GuiDeps, RequestContext, Router
from agentsassemble.web.routes.gui import install_gui_route_authorization


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

    def _send_error(self, status, message, **_kwargs):
        self.sent_error = (status, message)


class FakeRoomSessions:
    def verify(self, _token):
        return None


class TokenRoomSessions:
    def __init__(self, sessions):
        self.sessions = sessions

    def verify(self, token):
        return self.sessions.get(token)


def _context(handler, path="/api/test"):
    parsed = urlparse(path)
    deps = GuiDeps(
        output_root=Path("."),
        room_sessions=FakeRoomSessions(),
        public_invite_runtime=room_invite.compatibility_public_invite_runtime(),
    )
    return RequestContext(handler, deps, parsed, parse_qs(parsed.query))


class RouterDispatchTests(unittest.TestCase):
    def test_side_chat_store_enforces_time_and_count_retention(self):
        now = [datetime(2030, 1, 1, tzinfo=UTC)]
        store = SideChatStore(
            clock=lambda: now[0],
            max_events_per_room=2,
            ttl=timedelta(hours=1),
        )

        for message in ("first", "second"):
            store.append({"flow_meeting_id": "room-a", "message": message})
            now[0] += timedelta(minutes=20)
        self.assertEqual(
            [event["message"] for event in store.read("room-a")],
            ["first", "second"],
        )

        store.append({"flow_meeting_id": "room-a", "message": "third"})
        self.assertEqual(
            [event["message"] for event in store.read("room-a")],
            ["second", "third"],
        )

        now[0] += timedelta(hours=2)
        self.assertEqual(store.read("room-a"), [])

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

    def test_dynamic_dispatch_uses_production_template_matcher(self):
        router = Router()
        seen = []

        @router.post_dynamic("/api/groups/{group_id}/stop")
        def stop(ctx, path_params):
            seen.append(path_params)
            ctx.send_json({"stopped": path_params["group_id"]})

        handler = FakeHandler()
        self.assertTrue(router.dispatch("POST", _context(handler, "/api/groups/group-one/stop")))
        self.assertEqual(seen, [{"group_id": "group-one"}])
        self.assertEqual(handler.sent_json, {"stopped": "group-one"})
        self.assertFalse(router.dispatch("POST", _context(FakeHandler(), "/api/groups/group%2Fone/stop")))
        self.assertFalse(router.dispatch("POST", _context(FakeHandler(), "/api/not-groups/group/stop")))

    def test_duplicate_dynamic_registration_is_rejected(self):
        router = Router()
        router.add_dynamic("POST", "/api/groups/{group_id}/stop", lambda _ctx, _params: None)
        with self.assertRaises(ValueError):
            router.add_dynamic("POST", "/api/groups/{group_id}/stop", lambda _ctx, _params: None)

    def test_equivalent_dynamic_registration_is_rejected(self):
        router = Router()
        router.add_dynamic("POST", "/api/groups/{group_id}/stop", lambda _ctx, _params: None)
        with self.assertRaises(ValueError):
            router.add_dynamic("POST", "/api/groups/{name}/stop", lambda _ctx, _params: None)

    def test_dynamic_dispatch_rejects_unsafe_decoded_segments(self):
        router = Router()
        router.add_dynamic("POST", "/api/groups/{group_id}/stop", lambda _ctx, _params: None)

        for value in ("group%2Fone", "group%5Cone", "%2e", "%2e%2e", "%00", "a" * 257):
            with self.subTest(value=value):
                self.assertFalse(router.dispatch("POST", _context(FakeHandler(), f"/api/groups/{value}/stop")))

    def test_composed_policy_blocks_unclassified_remote_mutation_before_storage(self):
        class NetworkHandler(FakeHandler):
            def __init__(self, *, body: bytes, local: bool):
                host = "127.0.0.1:8765" if local else "room.example"
                origin = "http://127.0.0.1:8765" if local else "https://room.example"
                super().__init__(headers={"Host": host, "Origin": origin}, body=body)
                self.client_address = ("127.0.0.1" if local else "203.0.113.8", 43100)
                self.server = type(
                    "Server",
                    (),
                    {"server_address": (("127.0.0.1" if local else "0.0.0.0"), 8765)},
                )()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            body = json.dumps(
                {
                    "name": "caller",
                    "message": "must require local authority",
                    "flow_meeting_id": "room-a",
                }
            ).encode("utf-8")
            deps = GuiDeps(output_root=root, side_chat_store=SideChatStore())
            router = Router()
            router.post("/api/compatibility-only")(lambda ctx: ctx.send_json({"ok": True}))
            install_gui_route_authorization(router)

            remote = NetworkHandler(body=body, local=False)
            remote_ctx = RequestContext(
                remote,
                deps,
                urlparse("/api/compatibility-only"),
                {},
            )
            self.assertTrue(router.dispatch("POST", remote_ctx))
            self.assertEqual(remote.sent_error[0], HTTPStatus.FORBIDDEN)
            self.assertFalse((root / "side_chat.jsonl").exists())

            local = NetworkHandler(body=body, local=True)
            local_ctx = RequestContext(
                local,
                deps,
                urlparse("/api/compatibility-only"),
                {},
            )
            self.assertTrue(router.dispatch("POST", local_ctx))
            self.assertIsNone(local.sent_error)
            self.assertFalse((root / "side_chat.jsonl").exists())
            self.assertEqual(local.sent_json, {"ok": True})

    def test_side_chat_allows_room_browser_and_rejects_agent_bridge(self):
        class NetworkHandler(FakeHandler):
            def __init__(self, *, token: str, body: bytes = b""):
                super().__init__(
                    headers={
                        "Host": "room.example",
                        "Origin": "https://room.example",
                        "Authorization": f"Bearer {token}",
                    },
                    body=body,
                )
                self.client_address = ("203.0.113.8", 43100)
                self.server = type("Server", (), {"server_address": ("0.0.0.0", 8765)})()

        sessions = TokenRoomSessions(
            {
                "browser-token": {
                    "agent_id": "human-1",
                    "display_name": "Human",
                    "participant_type": "human",
                    "client_type": "browser",
                    "invite_scope": "read_write",
                    "meeting_id": "room-a",
                },
                "agent-token": {
                    "agent_id": "agent-1",
                    "display_name": "Agent",
                    "participant_type": "agent",
                    "client_type": "agent_bridge",
                    "invite_scope": "read_write",
                    "meeting_id": "room-a",
                },
            }
        )
        store = SideChatStore()
        deps = GuiDeps(
            output_root=Path("."),
            room_sessions=sessions,
            public_invite_runtime=room_invite.compatibility_public_invite_runtime(),
            side_chat_store=store,
        )
        router = Router()
        register_side_chat_routes(router)
        install_gui_route_authorization(router)

        body = json.dumps(
            {"name": "spoofed", "message": "human aside", "flow_meeting_id": "room-a"}
        ).encode()
        browser = NetworkHandler(token="browser-token", body=body)
        browser_ctx = RequestContext(browser, deps, urlparse("/api/side-chat"), {})
        self.assertTrue(router.dispatch("POST", browser_ctx))
        self.assertIsNone(browser.sent_error)
        self.assertEqual(browser.sent_json["event"]["name"], "Human")

        agent = NetworkHandler(token="agent-token")
        parsed = urlparse("/api/side-chat?meeting_id=room-a")
        agent_ctx = RequestContext(agent, deps, parsed, parse_qs(parsed.query))
        self.assertTrue(router.dispatch("GET", agent_ctx))
        self.assertEqual(agent.sent_error[0], HTTPStatus.FORBIDDEN)
        self.assertIsNone(agent.sent_json)

    def test_message_pins_allow_writable_people_and_reject_read_only_or_agents(self):
        class NetworkHandler(FakeHandler):
            def __init__(self, *, token: str, body: bytes = b""):
                super().__init__(
                    headers={
                        "Host": "room.example",
                        "Origin": "https://room.example",
                        "Authorization": f"Bearer {token}",
                    },
                    body=body,
                )
                self.client_address = ("203.0.113.8", 43100)
                self.server = type("Server", (), {"server_address": ("0.0.0.0", 8765)})()

        sessions = TokenRoomSessions(
            {
                "writer": {
                    "agent_id": "human-writer",
                    "participant_type": "human",
                    "client_type": "browser",
                    "invite_scope": "room",
                    "meeting_id": "room-a",
                },
                "reader": {
                    "agent_id": "human-reader",
                    "participant_type": "human",
                    "client_type": "browser",
                    "invite_scope": "read_only",
                    "meeting_id": "room-a",
                },
                "agent": {
                    "agent_id": "agent-a",
                    "participant_type": "agent",
                    "client_type": "browser",
                    "invite_scope": "room",
                    "meeting_id": "room-a",
                },
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rooms = RoomStore(root)
            rooms.create_room("room-a")
            message = rooms.append_event(
                "room-a",
                "message_final",
                participant_id="human-writer",
                participant_type="human",
                display_name="Writer",
                content="pin this",
                message_kind="message",
            )
            deps = GuiDeps(
                output_root=root,
                room_repository=rooms,
                room_sessions=sessions,
                public_invite_runtime=room_invite.compatibility_public_invite_runtime(),
            )
            router = Router()
            register_message_pin_routes(router)
            install_gui_route_authorization(router)

            body = json.dumps(
                {
                    "room_id": "room-a",
                    "channel_id": "lobby",
                    "event_id": message["id"],
                    "pinned": True,
                }
            ).encode()
            writer = NetworkHandler(token="writer", body=body)
            writer_ctx = RequestContext(writer, deps, urlparse("/api/room-pins"), {})
            self.assertTrue(router.dispatch("POST", writer_ctx))
            self.assertIsNone(writer.sent_error)
            self.assertEqual(writer.sent_json["pins"][0]["event_id"], message["id"])

            for token in ("reader", "agent"):
                denied = NetworkHandler(token=token, body=body)
                denied_ctx = RequestContext(denied, deps, urlparse("/api/room-pins"), {})
                self.assertTrue(router.dispatch("POST", denied_ctx))
                self.assertEqual(denied.sent_error[0], HTTPStatus.FORBIDDEN)

            parsed = urlparse("/api/room-pins?room_id=room-a&channel_id=lobby")
            reader = NetworkHandler(token="reader")
            reader_ctx = RequestContext(reader, deps, parsed, parse_qs(parsed.query))
            self.assertTrue(router.dispatch("GET", reader_ctx))
            self.assertEqual(reader.sent_json["pins"][0]["content"], "pin this")


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

    def test_read_json_body_runs_invalid_json_hook_before_response(self):
        events = []

        class OrderedHandler(FakeHandler):
            def _send_error(self, status, message):
                events.append("response")
                super()._send_error(status, message)

        handler = OrderedHandler(body=b"\xff")
        self.assertIsNone(
            _context(handler).read_json_body(
                before_invalid_json_response=lambda: events.append("hook"),
            )
        )

        self.assertEqual(events, ["hook", "response"])

    def test_read_json_body_empty_is_empty_dict(self):
        self.assertEqual(_context(FakeHandler()).read_json_body(), {})

    def test_read_json_body_can_preserve_legacy_empty_object_coercion(self):
        handler = FakeHandler(body=b"[1, 2]")
        self.assertEqual(_context(handler).read_json_body(coerce_non_object=True), {})
        self.assertIsNone(handler.sent_error)

    def test_read_json_body_rejects_invalid_content_length_without_reading(self):
        class UnreadableBody:
            def read(self, _length):
                raise AssertionError("invalid Content-Length must be rejected before reading")

        for content_length in ("-1", "not-a-number", "+12"):
            with self.subTest(content_length=content_length):
                handler = FakeHandler(headers={"Content-Length": content_length})
                handler.rfile = UnreadableBody()

                self.assertIsNone(_context(handler).read_json_body())
                self.assertEqual(handler.sent_error[0], HTTPStatus.BAD_REQUEST)

    def test_read_json_body_rejects_oversized_body_without_reading(self):
        class UnreadableBody:
            def read(self, _length):
                raise AssertionError("oversized request must be rejected before reading")

        for content_length in (str(100 * 1024 * 1024), "9" * 5000):
            with self.subTest(content_length_size=len(content_length)):
                handler = FakeHandler(headers={"Content-Length": content_length})
                handler.rfile = UnreadableBody()

                self.assertIsNone(_context(handler).read_json_body())
                self.assertEqual(handler.sent_error[0], HTTPStatus.REQUEST_ENTITY_TOO_LARGE)


class RequestContextIdentityTests(unittest.TestCase):
    def setUp(self):
        room_invite.reset_state()
        self.addCleanup(room_invite.reset_state)

    def test_identity_backend_must_be_injected(self):
        deps = GuiDeps(output_root=Path("."))

        with self.assertRaisesRegex(RuntimeError, "identity backend is not configured"):
            _ = deps.identities

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
