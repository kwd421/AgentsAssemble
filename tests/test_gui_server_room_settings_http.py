from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from agentsassemble.application.public_invite_runtime import PublicInviteRuntime
from agentsassemble.gui import _make_handler
from agentsassemble.web.routes.room_settings import register_room_settings_routes
from agentsassemble.web.router import GuiDeps, RequestContext, Router
from agentsassemble.persistence.local.identity.repository import IdentityStore
from agentsassemble.identity.repository import device_auth_key
from agentsassemble.room_store import RoomStore


class FakeHandler:
    def __init__(
        self,
        path: str,
        *,
        body: bytes = b"",
        device_token: str = "room-settings-test-device",
        local_operator: bool = True,
        host_token: str = "",
        session_room_id: str = "",
    ) -> None:
        self.path = path
        self.headers = {
            "Content-Length": str(len(body)),
            "X-Device-Token": device_token,
            "Host": "127.0.0.1:8765" if local_operator else "room.example",
        }
        if host_token:
            self.headers["X-Host-Token"] = host_token
        if session_room_id:
            self.headers["Authorization"] = "Bearer room-settings-session"
        self.server = SimpleNamespace(
            server_address=("127.0.0.1" if local_operator else "0.0.0.0", 8765)
        )
        self.client_address = (
            "127.0.0.1" if local_operator else "203.0.113.8",
            43100,
        )
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
        local_operator: bool = True,
        host_token: str = "",
        session_room_id: str = "",
    ) -> FakeHandler:
        handler = FakeHandler(
            path,
            body=body,
            device_token=device_token,
            local_operator=local_operator,
            host_token=host_token,
            session_room_id=session_room_id,
        )
        parsed = urlparse(path)
        repository = RoomStore(output_root)
        identities = IdentityStore(output_root / "identity.db")
        session: dict[str, object] | None = None
        if session_room_id:
            user = identities.resolve_credential_user(
                device_auth_key(device_token),
                provider="device",
                participant_type="human",
            )
            session = {
                "agent_id": str((user or {}).get("participant_id") or ""),
                "principal_user_id": str((user or {}).get("user_id") or ""),
                "meeting_id": session_room_id,
                "invite_scope": "room",
            }
        public_invite = PublicInviteRuntime(environ={})
        public_invite.set_host_token("host-secret")
        context = RequestContext(
            handler,
            GuiDeps(
                output_root=output_root,
                room_repository=repository,
                identity_backend=identities,
                public_invite_runtime=public_invite,
                room_sessions=SimpleNamespace(
                    verify=lambda token: session
                    if token == "room-settings-session"
                    else None
                ),
            ),
            parsed,
            parse_qs(parsed.query),
        )
        self.assertTrue(self.router.dispatch(method, context))
        return handler

    def test_post_then_get_roundtrip_persists_user_preferences(self) -> None:
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
            saved = self._dispatch(
                root,
                "/api/room-settings",
                "POST",
                body=json.dumps(preferences).encode(),
            )
            loaded = self._dispatch(root, "/api/room-settings?room_id=room-1", "GET")
            canonical = RoomStore(root).room_settings("room-1")

        settings = loaded.sent_json["settings"]
        self.assertEqual(saved.sent_json["settings"]["appearance"]["notifications"], "mute")
        self.assertEqual(settings["appearance"]["notifications"], "mute")
        self.assertEqual(settings["channel_settings"]["lobby"]["notifications"], "mentions")
        self.assertNotIn("notifications", canonical["appearance"])
        self.assertNotIn("channel_settings", canonical)

    def test_post_rejects_any_request_that_could_bypass_canonical_global_events(self) -> None:
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

        self.assertEqual(response.sent_error[0], HTTPStatus.CONFLICT)
        self.assertIn("canonical room WebSocket command", response.sent_error[1])

    def test_remote_anonymous_post_cannot_change_room_global_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = RoomStore(root)
            repository.create_room("room-1", label="Original")

            response = self._dispatch(
                root,
                "/api/room-settings",
                "POST",
                body=json.dumps({"room_id": "room-1", "label": "Unauthorized"}).encode(),
                local_operator=False,
            )
            stored_label = repository.room_settings("room-1")["label"]

        self.assertEqual(response.sent_error[0], HTTPStatus.UNAUTHORIZED)
        self.assertEqual(stored_label, "Original")

    def test_remote_host_cannot_bypass_the_canonical_global_event_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = RoomStore(root)
            repository.create_room("room-1", label="Original")

            response = self._dispatch(
                root,
                "/api/room-settings",
                "POST",
                body=json.dumps({"room_id": "room-1", "label": "Authorized"}).encode(),
                local_operator=False,
                host_token="host-secret",
            )
            stored_label = repository.room_settings("room-1")["label"]

        self.assertEqual(response.sent_error[0], HTTPStatus.CONFLICT)
        self.assertEqual(stored_label, "Original")

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
                local_operator=False,
                session_room_id="room-1",
            )

            first = self._dispatch(
                root,
                "/api/room-settings?room_id=room-1",
                "GET",
                device_token="device-user-alpha",
                local_operator=False,
                session_room_id="room-1",
            )
            second = self._dispatch(
                root,
                "/api/room-settings?room_id=room-1",
                "GET",
                device_token="device-user-bravo",
                local_operator=False,
                session_room_id="room-1",
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

    def test_http_global_setting_is_rejected_before_it_can_change_repository(self) -> None:
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

            self.assertEqual(response.sent_error[0], HTTPStatus.CONFLICT)
            self.assertIn("canonical room WebSocket command", response.sent_error[1])
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
    def test_live_http_preference_roundtrip_reaches_registered_routes(self) -> None:
        payload = {
            "room_id": "live-room",
            "appearance": {"notifications": "mute"},
        }
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

        self.assertEqual(saved["settings"]["appearance"]["notifications"], "mute")
        self.assertEqual(loaded["settings"]["appearance"]["notifications"], "mute")


if __name__ == "__main__":
    unittest.main()
