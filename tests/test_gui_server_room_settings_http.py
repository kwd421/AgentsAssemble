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
from agentsassemble.web.routes.room_settings import register_room_settings_routes
from agentsassemble.web.router import GuiDeps, RequestContext, Router
from agentsassemble.persistence.local.identity.repository import IdentityStore
from agentsassemble.room_store import RoomStore


class FakeHandler:
    def __init__(
        self,
        path: str,
        *,
        body: bytes = b"",
        device_token: str = "room-settings-test-device",
    ) -> None:
        self.path = path
        self.headers = {
            "Content-Length": str(len(body)),
            "X-Device-Token": device_token,
        }
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
        device_token: str = "room-settings-test-device",
    ) -> FakeHandler:
        handler = FakeHandler(path, body=body, device_token=device_token)
        parsed = urlparse(path)
        repository = RoomStore(output_root)
        context = RequestContext(
            handler,
            GuiDeps(
                output_root=output_root,
                room_repository=repository,
                identity_backend=IdentityStore(output_root / "identity.db"),
            ),
            parsed,
            parse_qs(parsed.query),
        )
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
        preferences = {
            "room_id": "room-1",
            "appearance": {"notifications": "mute"},
            "channel_settings": {
                "lobby": {"notifications": "mentions", "last_read_at": "2026-07-14"}
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            RoomStore(root).create_room("room-1", label="Room 1")
            saved = self._dispatch(root, "/api/room-settings", "POST", body=json.dumps(payload).encode())
            self._dispatch(
                root,
                "/api/room-settings",
                "POST",
                body=json.dumps(preferences).encode(),
            )
            loaded = self._dispatch(root, "/api/room-settings?room_id=room-1", "GET")
            canonical = RoomStore(root).room_settings("room-1")

        settings = loaded.sent_json["settings"]
        self.assertEqual(saved.sent_json["settings"]["label"], "Planning room")
        self.assertEqual(settings["label"], "Planning room")
        self.assertEqual(settings["topic"], "Ship the next slice")
        self.assertEqual(settings["appearance"]["banner_preset"], "forest")
        self.assertEqual(settings["appearance"]["notifications"], "mute")
        self.assertEqual(settings["channel_settings"]["lobby"]["notifications"], "mentions")
        self.assertNotIn("notifications", canonical["appearance"])
        self.assertNotIn("channel_settings", canonical)

    def test_post_rejects_mixed_global_and_user_preference_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            RoomStore(root).create_room("room-1", label="Room 1")

            response = self._dispatch(
                root,
                "/api/room-settings",
                "POST",
                body=json.dumps(
                    {
                        "room_id": "room-1",
                        "label": "Changed",
                        "appearance": {"notifications": "mute"},
                    }
                ).encode(),
            )

        self.assertEqual(response.sent_error[0], HTTPStatus.BAD_REQUEST)
        self.assertIn("separate requests", response.sent_error[1])

    def test_notification_and_read_preferences_are_isolated_by_device_user(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            RoomStore(root).create_room("room-1", label="Shared room")
            self._dispatch(
                root,
                "/api/room-settings",
                "POST",
                body=json.dumps(
                    {
                        "room_id": "room-1",
                        "appearance": {"notifications": "mute"},
                        "channel_settings": {
                            "lobby": {
                                "notifications": "all",
                                "last_read_at": "cursor-a",
                            }
                        },
                    }
                ).encode(),
                device_token="device-user-alpha",
            )

            first = self._dispatch(
                root,
                "/api/room-settings?room_id=room-1",
                "GET",
                device_token="device-user-alpha",
            )
            second = self._dispatch(
                root,
                "/api/room-settings?room_id=room-1",
                "GET",
                device_token="device-user-bravo",
            )

        self.assertEqual(first.sent_json["settings"]["appearance"]["notifications"], "mute")
        self.assertEqual(
            first.sent_json["settings"]["channel_settings"]["lobby"]["last_read_at"],
            "cursor-a",
        )
        self.assertEqual(second.sent_json["settings"]["appearance"]["notifications"], "mentions")
        self.assertEqual(second.sent_json["settings"]["channel_settings"], {})

    def test_camel_case_channel_preferences_are_canonicalized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            RoomStore(root).create_room("room-1", label="Room 1")

            response = self._dispatch(
                root,
                "/api/room-settings",
                "POST",
                body=json.dumps(
                    {
                        "room_id": "room-1",
                        "channelSettings": {
                            "lobby": {
                                "notifications": "mentions",
                                "lastReadAt": "cursor-camel",
                            }
                        },
                    }
                ).encode(),
            )

        self.assertEqual(
            response.sent_json["settings"]["channel_settings"]["lobby"],
            {"notifications": "mentions", "last_read_at": "cursor-camel"},
        )

    def test_conflicting_channel_preference_aliases_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            RoomStore(root).create_room("room-1", label="Room 1")

            response = self._dispatch(
                root,
                "/api/room-settings",
                "POST",
                body=json.dumps(
                    {
                        "room_id": "room-1",
                        "channel_settings": {},
                        "channelSettings": {
                            "lobby": {"notifications": "all", "lastReadAt": ""}
                        },
                    }
                ).encode(),
            )

        self.assertEqual(response.sent_error[0], HTTPStatus.BAD_REQUEST)
        self.assertIn("Conflicting room settings aliases", response.sent_error[1])

    def test_preference_update_without_stable_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            RoomStore(root).create_room("room-1", label="Room 1")

            response = self._dispatch(
                root,
                "/api/room-settings",
                "POST",
                body=json.dumps(
                    {
                        "room_id": "room-1",
                        "appearance": {"notifications": "mute"},
                    }
                ).encode(),
                device_token="",
            )

        self.assertEqual(response.sent_error[0], HTTPStatus.BAD_REQUEST)
        self.assertIn("stable user identity", response.sent_error[1])

    def test_invalid_global_setting_is_rejected_without_changing_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = RoomStore(root)
            repository.create_room("room-1", label="Room 1")

            response = self._dispatch(
                root,
                "/api/room-settings",
                "POST",
                body=json.dumps(
                    {"room_id": "room-1", "conversation_mode": "not-a-mode"}
                ).encode(),
            )

            self.assertEqual(response.sent_error[0], HTTPStatus.BAD_REQUEST)
            self.assertIn("Unsupported conversation_mode", response.sent_error[1])
            self.assertEqual(repository.room_settings("room-1")["conversation_mode"], "ordered")

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

    def test_get_missing_room_returns_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            response = self._dispatch(
                Path(temp_dir),
                "/api/room-settings?room_id=missing-room",
                "GET",
            )

        self.assertIsNone(response.sent_json)
        self.assertEqual(
            response.sent_error,
            (HTTPStatus.NOT_FOUND, "Room missing-room was not found."),
        )


class RoomSettingsHandlerDispatchTests(unittest.TestCase):
    def test_live_http_roundtrip_reaches_registered_routes(self) -> None:
        payload = {"room_id": "live-room", "label": "Live room", "topic": "Integration"}
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            RoomStore(root).create_room("live-room", label="Live room")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/room-settings"
                request = Request(
                    url,
                    data=json.dumps(payload).encode(),
                    method="POST",
                    headers={"X-Device-Token": "live-room-settings-device"},
                )
                with urlopen(request, timeout=4) as response:
                    saved = json.loads(response.read().decode())
                loaded_request = Request(
                    f"{url}?room_id=live-room",
                    headers={"X-Device-Token": "live-room-settings-device"},
                )
                with urlopen(loaded_request, timeout=4) as response:
                    loaded = json.loads(response.read().decode())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=4)

        self.assertEqual(saved["settings"]["label"], "Live room")
        self.assertEqual(loaded["settings"]["topic"], "Integration")


if __name__ == "__main__":
    unittest.main()
