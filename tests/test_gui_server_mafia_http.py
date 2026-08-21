from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, call, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

import agentsassemble.features.mafia.routes as mafia_http
from agentsassemble.gui import _make_handler
from agentsassemble.web.router import GuiDeps, RequestContext, Router
from agentsassemble.admission.invite import reset_state, set_runtime_public_url


class FakeHandler:
    def __init__(self, body: bytes = b"") -> None:
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.sent_json: dict[str, object] | None = None
        self.sent_error: tuple[HTTPStatus, str] | None = None

    def _send_json(self, payload: dict[str, object]) -> None:
        self.sent_json = payload

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self.sent_error = (status, message)


class MafiaHttpRegistrarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.output_root = Path(".")
        self.router = Router()
        self.operation_calls: list[tuple[RequestContext, str]] = []
        self.operation_payloads: dict[str, dict[str, object]] | None = {}

        def read_operation_payload(ctx: RequestContext, operation_name: str) -> dict[str, object] | None:
            self.operation_calls.append((ctx, operation_name))
            if self.operation_payloads is None:
                return None
            return self.operation_payloads.get(operation_name, {"game_id": "game"})

        mafia_http.register_mafia_routes(self.router, read_operation_payload=read_operation_payload)

    def _dispatch(self, method: str, path: str, *, body: bytes = b"") -> FakeHandler:
        handler = FakeHandler(body)
        parsed = urlparse(path)
        context = RequestContext(handler, GuiDeps(output_root=self.output_root), parsed, parse_qs(parsed.query))
        self.assertTrue(self.router.dispatch(method, context))
        return handler

    def test_operation_reader_receives_each_exact_post_operation_name(self) -> None:
        with patch.multiple(
            mafia_http,
            start_mafia_game=Mock(return_value={"game_id": "game"}),
            post_mafia_chat=Mock(return_value={"kind": "chat"}),
            cast_mafia_vote=Mock(return_value={"kind": "vote"}),
            submit_mafia_action=Mock(return_value={"kind": "action"}),
            resolve_mafia_phase=Mock(return_value={"game_id": "game"}),
            mafia_game_payload=Mock(return_value={"game_id": "game"}),
        ):
            for path in (
                "/api/play/mafia/start",
                "/api/play/mafia/chat",
                "/api/play/mafia/vote",
                "/api/play/mafia/action",
                "/api/play/mafia/resolve",
            ):
                self._dispatch("POST", path)

        self.assertEqual(
            [operation for _context, operation in self.operation_calls],
            ["mafia.start", "mafia.chat", "mafia.vote", "mafia.action", "mafia.resolve"],
        )
        self.assertTrue(all(isinstance(context, RequestContext) for context, _operation in self.operation_calls))

    def test_none_operation_payload_skips_domain_calls_and_response(self) -> None:
        self.operation_payloads = None
        with patch.multiple(
            mafia_http,
            start_mafia_game=Mock(),
            post_mafia_chat=Mock(),
            cast_mafia_vote=Mock(),
            submit_mafia_action=Mock(),
            resolve_mafia_phase=Mock(),
            mafia_game_payload=Mock(),
        ) as domain_calls:
            responses = [
                self._dispatch("POST", path)
                for path in (
                    "/api/play/mafia/start",
                    "/api/play/mafia/chat",
                    "/api/play/mafia/vote",
                    "/api/play/mafia/action",
                    "/api/play/mafia/resolve",
                )
            ]

        self.assertTrue(all(response.sent_json is None and response.sent_error is None for response in responses))
        self.assertTrue(all(not mock.called for mock in domain_calls.values()))

    def test_get_value_error_is_not_found_and_post_value_errors_are_bad_requests(self) -> None:
        with patch.object(mafia_http, "mafia_game_payload", side_effect=ValueError("missing game")):
            get_response = self._dispatch("GET", "/api/play/mafia?game_id=missing")
        self.assertEqual(get_response.sent_error, (HTTPStatus.NOT_FOUND, "missing game"))

        with patch.multiple(
            mafia_http,
            start_mafia_game=Mock(side_effect=ValueError("invalid start")),
            post_mafia_chat=Mock(side_effect=ValueError("invalid chat")),
            cast_mafia_vote=Mock(side_effect=ValueError("invalid vote")),
            submit_mafia_action=Mock(side_effect=ValueError("invalid action")),
            resolve_mafia_phase=Mock(side_effect=ValueError("invalid resolve")),
        ):
            responses = [
                self._dispatch("POST", path)
                for path in (
                    "/api/play/mafia/start",
                    "/api/play/mafia/chat",
                    "/api/play/mafia/vote",
                    "/api/play/mafia/action",
                    "/api/play/mafia/resolve",
                )
            ]

        self.assertEqual(
            [response.sent_error for response in responses],
            [
                (HTTPStatus.BAD_REQUEST, "invalid start"),
                (HTTPStatus.BAD_REQUEST, "invalid chat"),
                (HTTPStatus.BAD_REQUEST, "invalid vote"),
                (HTTPStatus.BAD_REQUEST, "invalid action"),
                (HTTPStatus.BAD_REQUEST, "invalid resolve"),
            ],
        )

    def test_event_game_responses_preserve_viewer_and_resolved_game_fallbacks(self) -> None:
        self.operation_payloads = {
            "mafia.chat": {
                "game_id": "chat-game",
                "speaker_id": "speaker-a",
                "viewer_agent_id": "chat-viewer",
            },
            "mafia.vote": {"game_id": "vote-game", "voter_id": "voter-a"},
            "mafia.action": {
                "game_id": "action-game",
                "actor_id": "actor-a",
                "viewer_agent_id": "action-viewer",
            },
            "mafia.resolve": {"game_id": "payload-game"},
        }
        game_payload = Mock(side_effect=lambda _root, game_id, *, viewer_agent_id: {"game_id": game_id})
        with patch.multiple(
            mafia_http,
            post_mafia_chat=Mock(return_value={"kind": "chat"}),
            cast_mafia_vote=Mock(return_value={"kind": "vote"}),
            submit_mafia_action=Mock(return_value={"kind": "action"}),
            resolve_mafia_phase=Mock(return_value={}),
            mafia_game_payload=game_payload,
        ):
            chat_response = self._dispatch("POST", "/api/play/mafia/chat")
            vote_response = self._dispatch("POST", "/api/play/mafia/vote")
            action_response = self._dispatch("POST", "/api/play/mafia/action")
            resolve_response = self._dispatch("POST", "/api/play/mafia/resolve")

        self.assertEqual(chat_response.sent_json, {"event": {"kind": "chat"}, "game": {"game_id": "chat-game"}})
        self.assertEqual(vote_response.sent_json, {"event": {"kind": "vote"}, "game": {"game_id": "vote-game"}})
        self.assertEqual(action_response.sent_json, {"event": {"kind": "action"}, "game": {"game_id": "action-game"}})
        self.assertEqual(resolve_response.sent_json, {"game": {"game_id": "payload-game"}})
        self.assertEqual(
            game_payload.call_args_list,
            [
                call(self.output_root, "chat-game", viewer_agent_id="chat-viewer"),
                call(self.output_root, "vote-game", viewer_agent_id="voter-a"),
                call(self.output_root, "action-game", viewer_agent_id="action-viewer"),
                call(self.output_root, "payload-game", viewer_agent_id=""),
            ],
        )


class MafiaHttpHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_state()
        self.addCleanup(reset_state)

    @staticmethod
    def _request(request: Request) -> tuple[int, dict[str, object]]:
        try:
            with urlopen(request, timeout=4) as response:
                return response.status, json.loads(response.read().decode())
        except HTTPError as error:
            try:
                return error.code, json.loads(error.read().decode())
            finally:
                error.close()

    def test_public_host_is_rejected_before_mafia_route_dispatch(self) -> None:
        set_runtime_public_url("https://public.example.test")
        with tempfile.TemporaryDirectory() as temp_dir:
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(Path(temp_dir)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/play/mafia?game_id=game",
                    headers={"Host": "public.example.test"},
                )
                with patch.object(mafia_http, "mafia_game_payload") as game_payload:
                    status, payload = self._request(request)
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(status, HTTPStatus.FORBIDDEN)
        self.assertEqual(payload, {"error": "Untrusted request host or origin"})
        game_payload.assert_not_called()

    def test_malformed_post_returns_bad_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            handler_class = _make_handler(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/play/mafia/start",
                    data=b"{bad",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                status, payload = self._request(request)
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(payload, {"error": "Invalid JSON"})


if __name__ == "__main__":
    unittest.main()
