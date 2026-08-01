from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from agentsassemble.features.social.routes import (
    register_room_friend_profile_routes,
)
from agentsassemble.gui import _make_handler
from agentsassemble.web.router import GuiDeps, RequestContext, Router
from agentsassemble.admission.invite import reset_state, set_runtime_public_url


class _FakeHandler:
    def __init__(self, path: str, *, body: bytes = b"") -> None:
        self.path = path
        self.command = ""
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.sent_json: dict[str, object] | None = None
        self.sent_error: tuple[HTTPStatus, str] | None = None

    def _send_json(self, payload: dict[str, object]) -> None:
        self.sent_json = payload

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self.sent_error = (status, message)


class SocialHttpRegistrarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = Router()
        self.direct_dm_calls: list[tuple[RequestContext, dict[str, object]]] = []

        def post_direct_dm(ctx: RequestContext, payload: dict[str, object]) -> dict[str, object]:
            self.direct_dm_calls.append((ctx, payload))
            return {"accepted": True}

        register_room_friend_profile_routes(self.router, post_direct_dm=post_direct_dm)

    def _dispatch(
        self,
        output_root: Path,
        path: str,
        method: str,
        *,
        body: bytes = b"",
    ) -> _FakeHandler:
        handler = _FakeHandler(path, body=body)
        handler.command = method
        parsed = urlparse(path)
        context = RequestContext(handler, GuiDeps(output_root=output_root), parsed, parse_qs(parsed.query))
        self.assertTrue(self.router.dispatch(method, context))
        return handler

    def test_registers_exactly_the_seven_social_routes(self) -> None:
        self.assertEqual(
            set(self.router.routes()),
            {
                ("GET", "/api/room-friends"),
                ("POST", "/api/room-friends"),
                ("DELETE", "/api/room-friends"),
                ("GET", "/api/room-friends/dm"),
                ("POST", "/api/room-friends/dm"),
                ("GET", "/api/user-profile"),
                ("POST", "/api/user-profile"),
            },
        )

    def test_friend_routes_use_the_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            saved = self._dispatch(
                root,
                "/api/room-friends",
                "POST",
                body=json.dumps({"friend_id": "friend:sei", "display_name": "SeiNel"}).encode(),
            )
            loaded_friends = self._dispatch(root, "/api/room-friends", "GET")

        self.assertEqual(saved.sent_json["friend"]["friend_id"], "friend:sei")
        self.assertEqual(loaded_friends.sent_json["friends"][0]["display_name"], "SeiNel")

    def test_all_post_routes_reject_malformed_and_non_object_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for path in ("/api/room-friends", "/api/room-friends/dm", "/api/user-profile"):
                for body in (b"{bad", b"[]"):
                    with self.subTest(path=path, body=body):
                        response = self._dispatch(root, path, "POST", body=body)
                        self.assertEqual(response.sent_error, (HTTPStatus.BAD_REQUEST, "Invalid JSON"))

    def test_missing_and_unknown_friend_errors_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cases = (
                ("GET", "/api/room-friends/dm", "friend_id is required"),
                ("GET", "/api/room-friends/dm?friend_id=unknown", "Saved room friend was not found"),
                ("DELETE", "/api/room-friends", "friend_id is required"),
                ("DELETE", "/api/room-friends?friend_id=unknown", "Friend not found"),
            )
            for method, path, message in cases:
                with self.subTest(method=method, path=path):
                    response = self._dispatch(root, path, method)
                    self.assertEqual(response.sent_error, (HTTPStatus.BAD_REQUEST, message))

    def test_injected_direct_dm_callback_receives_context_and_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = {"friend_id": "friend:sei", "message": "hello"}
            response = self._dispatch(
                root,
                "/api/room-friends/dm",
                "POST",
                body=json.dumps(payload).encode(),
            )

        self.assertEqual(response.sent_json, {"accepted": True})
        context, received_payload = self.direct_dm_calls[0]
        self.assertIsInstance(context, RequestContext)
        self.assertIs(context.deps.output_root, root)
        self.assertEqual(received_payload, payload)


class SocialHttpHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_state()
        self.addCleanup(reset_state)

    @staticmethod
    def _request(
        url: str,
        *,
        method: str,
        payload: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(url, data=data, headers=headers or {}, method=method)
        try:
            with urlopen(request, timeout=4) as response:
                return response.status, json.loads(response.read().decode())
        except HTTPError as error:
            try:
                return error.code, json.loads(error.read().decode())
            finally:
                error.close()

    def test_late_direct_dm_adapter_resolves_gui_global_after_handler_construction(self) -> None:
        supervisor = object()
        calls: list[tuple[Path, object, dict[str, object], str]] = []

        def patched_direct_dm(
            output_root: Path,
            received_supervisor: object,
            payload: dict[str, object],
            *,
            default_server: str,
        ) -> dict[str, object]:
            calls.append((output_root, received_supervisor, payload, default_server))
            return {"patched": True}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            handler_class = _make_handler(root, process_supervisor=supervisor)
            with patch("agentsassemble.gui.room_friend_direct_dm_payload", patched_direct_dm):
                server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{server.server_port}"
                    status, payload = self._request(
                        f"{base}/api/room-friends/dm",
                        method="POST",
                        payload={"friend_id": "friend:sei", "message": "hello"},
                    )
                finally:
                    server.shutdown()
                    server.server_close()

        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload, {"patched": True})
        self.assertEqual(calls, [(root, supervisor, {"friend_id": "friend:sei", "message": "hello"}, base)])

    def test_public_host_rejects_friends_and_requires_profile_authentication(self) -> None:
        set_runtime_public_url("https://public.example.test")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                for method, path, payload in (
                    ("GET", "/api/room-friends", None),
                    ("POST", "/api/room-friends", {}),
                    ("DELETE", "/api/room-friends?friend_id=friend:sei", None),
                    ("GET", "/api/room-friends/dm?friend_id=friend:sei", None),
                    ("POST", "/api/room-friends/dm", {}),
                ):
                    with self.subTest(method=method, path=path):
                        status, _response = self._request(
                            f"{base}{path}",
                            method=method,
                            payload=payload,
                            headers={"Host": "public.example.test"},
                        )
                        self.assertEqual(status, HTTPStatus.FORBIDDEN)
                for method, payload in (("GET", None), ("POST", {})):
                    with self.subTest(method=method, path="/api/user-profile"):
                        status, _response = self._request(
                            f"{base}/api/user-profile",
                            method=method,
                            payload=payload,
                            headers={"Host": "public.example.test"},
                        )
                        self.assertEqual(status, HTTPStatus.UNAUTHORIZED)
            finally:
                server.shutdown()
                server.server_close()

    def test_delete_dm_and_profile_remain_unregistered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                for path in ("/api/room-friends/dm", "/api/user-profile"):
                    with self.subTest(path=path):
                        status, response = self._request(f"{base}{path}", method="DELETE")
                        self.assertEqual(status, HTTPStatus.NOT_FOUND)
                        self.assertEqual(response, {"error": "Not found"})
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
