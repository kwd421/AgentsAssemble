from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from agentsassemble.gui import _make_handler
from agentsassemble.gui_room_settings_http import register_room_settings_routes
from agentsassemble.gui_router import GuiDeps, RequestContext, Router


class FakeHandler:
    def __init__(self, path: str, *, body: bytes = b"") -> None:
        self.path = path
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.sent_json: dict[str, object] | None = None
        self.sent_error: tuple[HTTPStatus, str] | None = None

    def _send_json(self, payload: dict[str, object]) -> None:
        self.sent_json = payload

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self.sent_error = (status, message)


class RoomSettingsHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = Router()
        register_room_settings_routes(self.router)

    def _dispatch(
        self,
        output_root: Path,
        path: str,
        method: str,
        *,
        body: bytes = b"",
    ) -> FakeHandler:
        handler = FakeHandler(path, body=body)
        parsed = urlparse(path)
        context = RequestContext(handler, GuiDeps(output_root=output_root), parsed, parse_qs(parsed.query))
        self.assertTrue(self.router.dispatch(method, context))
        return handler

    def test_registers_exactly_the_two_room_settings_routes(self) -> None:
        self.assertEqual(
            set(self.router.routes()),
            {
                ("GET", "/api/room-settings"),
                ("POST", "/api/room-settings"),
            },
        )

    def test_post_then_get_roundtrip_persists_room_settings(self) -> None:
        payload = {
            "room_id": "room-1",
            "label": "Planning room",
            "topic": "Ship the next slice",
            "appearance": {"banner_preset": "forest"},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            saved = self._dispatch(root, "/api/room-settings", "POST", body=json.dumps(payload).encode())
            loaded = self._dispatch(root, "/api/room-settings?room_id=room-1", "GET")

        settings = loaded.sent_json["settings"]
        self.assertEqual(saved.sent_json["settings"]["label"], "Planning room")
        self.assertEqual(settings["label"], "Planning room")
        self.assertEqual(settings["topic"], "Ship the next slice")
        self.assertEqual(settings["appearance"]["banner_preset"], "forest")

    def test_post_rejects_malformed_and_non_object_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for body in (b"{bad", b"[]"):
                with self.subTest(body=body):
                    response = self._dispatch(root, "/api/room-settings", "POST", body=body)
                    self.assertEqual(response.sent_error, (HTTPStatus.BAD_REQUEST, "Invalid JSON"))

    def test_post_requires_room_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for body in (b"", b"{}"):
                with self.subTest(body=body):
                    response = self._dispatch(root, "/api/room-settings", "POST", body=body)
                    self.assertEqual(response.sent_error, (HTTPStatus.BAD_REQUEST, "room_id is required"))


class RoomSettingsHandlerDispatchTests(unittest.TestCase):
    def test_live_http_roundtrip_reaches_registered_routes(self) -> None:
        payload = {"room_id": "live-room", "label": "Live room", "topic": "Integration"}
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/room-settings"
                with urlopen(Request(url, data=json.dumps(payload).encode(), method="POST"), timeout=4) as response:
                    saved = json.loads(response.read().decode())
                with urlopen(f"{url}?room_id=live-room", timeout=4) as response:
                    loaded = json.loads(response.read().decode())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=4)

        self.assertEqual(saved["settings"]["label"], "Live room")
        self.assertEqual(loaded["settings"]["topic"], "Integration")


if __name__ == "__main__":
    unittest.main()
