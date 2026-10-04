from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from agentsassemble.features.social.routes import (
    register_room_friend_profile_routes,
)
from agentsassemble.gui import _make_handler
from agentsassemble.application.public_invite_runtime import PublicInviteRuntime
from agentsassemble.web.router import GuiDeps, RequestContext, Router


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
        register_room_friend_profile_routes(self.router)

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
            for path in ("/api/room-friends", "/api/user-profile"):
                for body in (b"{bad", b"[]"):
                    with self.subTest(path=path, body=body):
                        response = self._dispatch(root, path, "POST", body=body)
                        self.assertEqual(response.sent_error, (HTTPStatus.BAD_REQUEST, "Invalid JSON"))

    def test_missing_and_unknown_friend_errors_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cases = (
                ("DELETE", "/api/room-friends", "friend_id is required"),
                ("DELETE", "/api/room-friends?friend_id=unknown", "Friend not found"),
            )
            for method, path, message in cases:
                with self.subTest(method=method, path=path):
                    response = self._dispatch(root, path, method)
                    self.assertEqual(response.sent_error, (HTTPStatus.BAD_REQUEST, message))

class SocialHttpHandlerTests(unittest.TestCase):
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

    def test_public_host_rejects_friends_and_requires_profile_authentication(self) -> None:
        public_invite = PublicInviteRuntime()
        public_invite.set_public_url("https://public.example.test")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, public_invite_runtime_override=public_invite),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                for method, path, payload in (
                    ("GET", "/api/room-friends", None),
                    ("POST", "/api/room-friends", {}),
                    ("DELETE", "/api/room-friends?friend_id=friend:sei", None),
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

    def test_delete_profile_remains_unregistered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                status, response = self._request(f"{base}/api/user-profile", method="DELETE")
                self.assertEqual(status, HTTPStatus.NOT_FOUND)
                self.assertEqual(response, {"error": "Not found"})
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
