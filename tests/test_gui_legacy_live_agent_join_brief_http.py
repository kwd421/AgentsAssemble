import io
import unittest
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentsassemble.legacy.live_agent.http.join_brief import (
    register_legacy_live_agent_join_brief_route,
)
from agentsassemble.web.router import GuiDeps, RequestContext, Router
from agentsassemble.legacy.live_agent.runtime.join_brief import live_agent_join_brief_payload


class FakeHandler:
    def __init__(self, body: bytes) -> None:
        self.path = "/api/live-agent-join-brief"
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.sent_json: dict[str, object] | None = None
        self.sent_error: tuple[HTTPStatus, str] | None = None

    def _send_json(self, payload: dict[str, object]) -> None:
        self.sent_json = payload

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self.sent_error = (status, message)


class LegacyLiveAgentJoinBriefRouteTests(unittest.TestCase):

    def dispatch(self, body: bytes) -> FakeHandler:
        router = Router()
        register_legacy_live_agent_join_brief_route(
            router,
            request_server_url=lambda _ctx: "http://room.local:8765",
        )
        handler = FakeHandler(body)
        parsed = urlparse(handler.path)
        context = RequestContext(
            handler,
            GuiDeps(output_root=Path(".")),
            parsed,
            parse_qs(parsed.query),
        )
        self.assertTrue(router.dispatch("POST", context))
        return handler

    def test_route_uses_request_server_and_returns_safe_packet(self) -> None:
        response = self.dispatch(
            b'{"agent_id":"agent-a","display_name":"Agent A","timeout":9}'
        )

        self.assertIsNone(response.sent_error)
        self.assertEqual(response.sent_json["agent"]["agent_id"], "agent-a")
        self.assertEqual(
            response.sent_json["commands"]["register"][5:7],
            ["--server", "http://room.local:8765"],
        )
        self.assertEqual(response.sent_json["safety"]["room_contacted"], False)
        self.assertEqual(response.sent_json["safety"]["provider_executed"], False)

    def test_explicit_server_overrides_request_server(self) -> None:
        response = self.dispatch(
            b'{"agent_id":"agent-a","server":"http://agent-room.local:9000"}'
        )

        self.assertEqual(
            response.sent_json["commands"]["register"][5:7],
            ["--server", "http://agent-room.local:9000"],
        )

    def test_invalid_json_keeps_bad_request_contract(self) -> None:
        response = self.dispatch(b"{bad")

        self.assertEqual(response.sent_error, (HTTPStatus.BAD_REQUEST, "Invalid JSON"))
        self.assertIsNone(response.sent_json)

    def test_non_object_json_keeps_bad_request_contract(self) -> None:
        response = self.dispatch(b"[]")

        self.assertEqual(response.sent_error, (HTTPStatus.BAD_REQUEST, "Invalid JSON"))

    def test_invalid_scalar_is_rejected_without_echoing_its_value(self) -> None:
        response = self.dispatch(
            b'{"agent_id":"agent-a","display_name":{"prompt":"private reply"}}'
        )

        self.assertEqual(
            response.sent_error,
            (HTTPStatus.BAD_REQUEST, "display_name must be a string."),
        )
        self.assertNotIn("private reply", response.sent_error[1])


class LiveAgentJoinBriefPayloadTests(unittest.TestCase):
    def test_payload_defaults_match_the_external_entry_contract(self) -> None:
        packet = live_agent_join_brief_payload(
            {"agent_id": "agent-a"},
            default_server="http://room.local",
        )

        self.assertEqual(
            packet["agent"],
            {
                "agent_id": "agent-a",
                "display_name": "agent-a",
                "provider_kind": "manual",
                "connection_kind": "manual",
                "meeting_id": "",
                "engagement_mode": "mentioned",
            },
        )
        self.assertEqual(packet["admission_contract"]["identity_proof"], "not_included_in_join_brief")


if __name__ == "__main__":
    unittest.main()
