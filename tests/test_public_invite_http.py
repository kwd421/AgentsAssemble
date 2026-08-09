import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from agentsassemble.gui import _make_handler
from agentsassemble.application.public_invite_runtime import PublicInviteRuntime
from agentsassemble.application.public_tunnel import PublicTunnelManager
from agentsassemble.admission.invite import get_public_url, reset_state, set_runtime_host_token, set_runtime_public_url
from agentsassemble.room_store import RoomStore
from agentsassemble.web.room_client import WsRoomClient, connect_room_ws_with_ticket


def _json_request(url: str, payload: dict[str, object], headers: dict[str, str] | None = None) -> Request:
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    return Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )


class PublicInviteHttpTests(unittest.TestCase):
    def setUp(self):
        reset_state()

    def tearDown(self):
        reset_state()

    def _start_server(self, root: Path, *, dist: Path | None = None, tunnel: PublicTunnelManager | None = None):
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            _make_handler(root, frontend_dist_root=dist, public_tunnel_manager=tunnel),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server

    def _ws_ticket(
        self,
        base: str,
        session_token: str,
        headers: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, str]]:
        with urlopen(
            _json_request(
                f"{base}/api/ws-ticket",
                {},
                {
                    **(headers or {}),
                    "Authorization": f"Bearer {session_token}",
                },
            ),
            timeout=4,
        ) as response:
            return (
                str(json.loads(response.read().decode("utf-8"))["ticket"]),
                dict(response.headers.items()),
            )

    def _room_command_result(
        self,
        client: WsRoomClient,
        request_id: str,
    ) -> dict[str, object]:
        for _ in range(20):
            for message in client.receive():
                if str(message.get("request_id") or "") == request_id:
                    return message
        raise AssertionError(f"room command {request_id} was not acknowledged")

    def test_external_invite_requires_public_url_and_never_returns_local_join_url(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            set_runtime_host_token("host-secret")
            server = self._start_server(root)
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with self.assertRaises(HTTPError) as error_context:
                    urlopen(
                        _json_request(
                            f"{base}/api/room-invite/create",
                            {"meeting_id": "friend-room", "display_name": "Friend"},
                            {"X-Host-Token": "host-secret"},
                        ),
                        timeout=4,
                    )
                self.addCleanup(error_context.exception.close)
                error_payload = json.loads(error_context.exception.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(error_context.exception.code, 409)
        self.assertIn("public URL is required before creating an external guest invite", error_payload["error"])

    def test_room_invite_create_uses_public_url_with_host_token(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            RoomStore(root).create_room("friend-room", label="Friend room")
            set_runtime_host_token("host-secret")
            set_runtime_public_url("https://shared-room.example.com")
            server = self._start_server(root)
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(
                    _json_request(
                        f"{base}/api/room-invite/create",
                        {"meeting_id": "friend-room", "display_name": "Friend"},
                        {"X-Host-Token": "host-secret"},
                    ),
                    timeout=4,
                ) as response:
                    invite = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertTrue(invite["join_url"].startswith("https://shared-room.example.com/join?token="))
        self.assertNotIn("env", invite["remote_client_packet"])
        self.assertEqual(invite["remote_client_packet"]["attend"]["live_transport"], "websocket_push")

    def test_invite_admission_recognizes_claimed_device_without_issuing_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            RoomStore(root).create_room("friend-room", label="Friend room")
            set_runtime_host_token("host-secret")
            set_runtime_public_url("https://shared-room.example.com")
            server = self._start_server(root)
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(
                    _json_request(
                        f"{base}/api/host/claim",
                        {"device_token": "same-origin-host-device"},
                        {"X-Host-Token": "host-secret"},
                    ),
                    timeout=4,
                ):
                    pass
                with urlopen(
                    _json_request(
                        f"{base}/api/room-invite/create",
                        {"meeting_id": "friend-room", "display_name": "Friend"},
                        {"X-Host-Token": "host-secret"},
                    ),
                    timeout=4,
                ) as response:
                    invite = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    _json_request(
                        f"{base}/api/room-invite/admission",
                        {"invite_token": invite["join_url"].split("token=", 1)[1]},
                        {"X-Device-Token": "same-origin-host-device"},
                    ),
                    timeout=4,
                ) as response:
                    decision = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(decision["status"], "known_user")
        self.assertEqual(decision["participant"]["participant_id"], "operator-local")
        self.assertTrue(decision["operator"])
        self.assertNotIn("session_token", decision)

    def test_public_origin_operator_pairing_resumes_only_for_the_bound_device(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            RoomStore(root).create_room("friend-room", label="Friend room")
            set_runtime_host_token("host-secret")
            set_runtime_public_url("https://shared-room.example.com")
            server = self._start_server(root)
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                host_headers = {"X-Host-Token": "host-secret"}
                with urlopen(
                    _json_request(
                        f"{base}/api/host/claim",
                        {"device_token": "local-operator-device"},
                        host_headers,
                    ),
                    timeout=4,
                ):
                    pass
                with urlopen(
                    _json_request(
                        f"{base}/api/operator-pairing/create",
                        {"meeting_id": "friend-room"},
                        host_headers,
                    ),
                    timeout=4,
                ) as response:
                    pairing = json.loads(response.read().decode("utf-8"))
                pairing_token = pairing["pairing_url"].split("token=", 1)[1]
                with self.assertRaises(HTTPError) as missing_origin_error:
                    urlopen(
                        _json_request(
                            f"{base}/api/operator-pairing/redeem",
                            {
                                "pairing_token": pairing_token,
                                "origin": "https://shared-room.example.com",
                            },
                            {
                                "Host": "shared-room.example.com",
                                "X-Device-Token": "public-origin-device",
                            },
                        ),
                        timeout=4,
                    )
                self.addCleanup(missing_origin_error.exception.close)
                public_headers = {
                    "Host": "shared-room.example.com",
                    "Origin": "https://shared-room.example.com",
                    "X-Device-Token": "public-origin-device",
                }
                with urlopen(
                    _json_request(
                        f"{base}/api/operator-pairing/redeem",
                        {
                            "pairing_token": pairing_token,
                            "origin": "https://forged-body.example.com",
                        },
                        public_headers,
                    ),
                    timeout=4,
                ) as response:
                    admitted = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    _json_request(
                        f"{base}/api/room-invite/create",
                        {"meeting_id": "friend-room", "display_name": "Paired moderator invite"},
                        {
                            **public_headers,
                            "Authorization": f"Bearer {admitted['session_token']}",
                        },
                    ),
                    timeout=4,
                ) as response:
                    moderator_invite = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    _json_request(
                        f"{base}/api/operator-pairing/redeem",
                        {
                            "pairing_token": pairing_token,
                            "origin": "https://shared-room.example.com",
                        },
                        public_headers,
                    ),
                    timeout=4,
                ) as response:
                    resumed = json.loads(response.read().decode("utf-8"))
                with self.assertRaises(HTTPError) as other_device_error:
                    urlopen(
                        _json_request(
                            f"{base}/api/operator-pairing/redeem",
                            {
                                "pairing_token": pairing_token,
                                "origin": "https://shared-room.example.com",
                            },
                            {
                                **public_headers,
                                "X-Device-Token": "different-public-device",
                            },
                        ),
                        timeout=4,
                    )
                self.addCleanup(other_device_error.exception.close)
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(pairing["target_origin"], "https://shared-room.example.com")
        self.assertEqual(missing_origin_error.exception.code, 403)
        self.assertIn(
            "pairing_origin_required",
            missing_origin_error.exception.read().decode("utf-8"),
        )
        self.assertEqual(admitted["agent_id"], "operator-local")
        self.assertTrue(admitted["operator"])
        self.assertEqual(resumed["session_token"], admitted["session_token"])
        self.assertEqual(admitted["room_label"], "Friend room")
        self.assertEqual(moderator_invite["meeting_id"], "friend-room")
        self.assertEqual(other_device_error.exception.code, 403)

    def test_host_token_bootstrap_rejects_untrusted_public_request(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            server = self._start_server(root)
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with self.assertRaises(HTTPError) as error_context:
                    urlopen(
                        _json_request(
                            f"{base}/api/public-invite/host-token",
                            {},
                            {
                                "Host": "evil.example.com",
                                "Origin": "https://evil.example.com",
                            },
                        ),
                        timeout=4,
                    )
                self.addCleanup(error_context.exception.close)
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(error_context.exception.code, 403)

    def test_tunnel_start_bootstraps_runtime_host_token_for_local_operator(self):
        class FakeTunnel:
            def __init__(self) -> None:
                self.local_url = ""

            def set_local_url(self, local_url: str) -> None:
                self.local_url = local_url

            def status(self) -> dict[str, object]:
                return {
                    "available": True,
                    "running": False,
                    "phase": "stopped",
                    "public_url": "",
                    "local_url": self.local_url,
                    "last_error": "",
                }

            def start(self) -> dict[str, object]:
                return {
                    "available": True,
                    "running": True,
                    "phase": "starting",
                    "public_url": "",
                    "local_url": self.local_url,
                    "last_error": "",
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            server = self._start_server(root, tunnel=FakeTunnel())  # type: ignore[arg-type]
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(_json_request(f"{base}/api/public-invite/tunnel/start", {}), timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["host_token"])
        self.assertTrue(payload["public_invite"]["host_token_configured"])
        self.assertEqual(payload["public_invite"]["tunnel"]["phase"], "starting")

    def test_public_url_endpoint_rejects_loopback_urls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            set_runtime_host_token("host-secret")
            server = self._start_server(root)
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with self.assertRaises(HTTPError) as error_context:
                    urlopen(
                        _json_request(
                            f"{base}/api/public-invite/public-url",
                            {"public_url": "http://127.0.0.1:8765"},
                            {"X-Host-Token": "host-secret"},
                        ),
                        timeout=4,
                    )
                self.addCleanup(error_context.exception.close)
                error_payload = json.loads(error_context.exception.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(error_context.exception.code, 400)
        self.assertIn("public invite URL must not use a local or loopback host", error_payload["error"])

    def test_serve_gui_startup_public_url_sets_runtime_public_url(self):
        from agentsassemble.gui import serve_gui

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = PublicInviteRuntime(environ={})

            def stop_after_bind() -> None:
                raise KeyboardInterrupt()

            with (
                patch("agentsassemble.gui.PublicInviteRuntime", return_value=runtime),
                patch("agentsassemble.gui.ThreadingHTTPServer.serve_forever", side_effect=stop_after_bind),
            ):
                serve_gui(
                    host="127.0.0.1",
                    port=0,
                    output_root=Path(temp_dir),
                    public_url="https://shared-room.example.com",
                    host_token="host-secret",
                )

        self.assertEqual(runtime.public_url(), "https://shared-room.example.com")

    def test_gui_can_bootstrap_host_token_public_url_and_join_link(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENTSASSEMBLE_HOST_TOKEN", None)
            os.environ.pop("AGENTSASSEMBLE_PUBLIC_URL", None)
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir) / "room"
                RoomStore(root).create_room("friend-room", label="Friend room")
                dist = Path(temp_dir) / "dist"
                assets = dist / "assets"
                assets.mkdir(parents=True)
                (dist / "index.html").write_text(
                    '<div id="root">react app</div><script type="module" src="/assets/app.js"></script>',
                    encoding="utf-8",
                )
                (assets / "app.js").write_text("console.log('join');", encoding="utf-8")
                runtime = PublicInviteRuntime(environ={})
                tunnel = PublicTunnelManager(
                    public_invite_runtime=runtime,
                    which=lambda _name: None,
                )
                server = ThreadingHTTPServer(
                    ("127.0.0.1", 0),
                    _make_handler(
                        root,
                        frontend_dist_root=dist,
                        public_tunnel_manager=tunnel,
                        public_invite_runtime_override=runtime,
                    ),
                )
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{server.server_port}"
                    with urlopen(_json_request(f"{base}/api/public-invite/host-token", {}), timeout=4) as response:
                        token_payload = json.loads(response.read().decode("utf-8"))
                    host_token = token_payload["host_token"]
                    self.assertEqual(token_payload["status"], "generated")

                    public_headers = {
                        "Host": "shared-room.example.com",
                        "Origin": "https://shared-room.example.com",
                    }
                    with urlopen(
                        _json_request(
                            f"{base}/api/public-invite/public-url",
                            {"public_url": "https://shared-room.example.com"},
                            {"X-Host-Token": host_token},
                        ),
                        timeout=4,
                    ) as response:
                        public_payload = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(public_payload["public_url"], "https://shared-room.example.com")

                    with urlopen(
                        _json_request(
                            f"{base}/api/room-invite/create",
                            {"meeting_id": "friend-room", "display_name": "Friend"},
                            {"X-Host-Token": host_token},
                        ),
                        timeout=4,
                    ) as response:
                        invite = json.loads(response.read().decode("utf-8"))
                    self.assertTrue(invite["join_url"].startswith("https://shared-room.example.com/join?token="))

                    with urlopen(
                        Request(
                            f"{base}/join?token={invite['invite_token']}",
                            headers={"Host": "shared-room.example.com"},
                        ),
                        timeout=4,
                    ) as response:
                        html = response.read().decode("utf-8")
                    self.assertIn("react app", html)

                    with self.assertRaises(HTTPError) as blocked_operator_route:
                        urlopen(Request(f"{base}/api/lobby", headers=public_headers), timeout=4)
                    self.addCleanup(blocked_operator_route.exception.close)
                    self.assertEqual(blocked_operator_route.exception.code, 403)

                    for blocked_path in (
                        "/api/side-chat",
                        "/api/room-friends/dm?friend_id=friend%3Aagent",
                    ):
                        with self.assertRaises(HTTPError) as blocked_private_get:
                            urlopen(Request(f"{base}{blocked_path}", headers=public_headers), timeout=4)
                        self.addCleanup(blocked_private_get.exception.close)
                        self.assertEqual(blocked_private_get.exception.code, 403)

                    with urlopen(
                        _json_request(
                            f"{base}/api/room-invite/join",
                            {
                                "invite_token": invite["invite_token"],
                                "request_id": str(uuid4()),
                                "device_token": "public-bootstrap-device",
                            },
                            public_headers,
                        ),
                        timeout=4,
                    ) as response:
                        session_payload = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(session_payload["status"], "admitted")
                    self.assertEqual(session_payload["room_label"], "Friend room")
                    self.assertIn("room_created_at", session_payload)

                    with self.assertRaises(HTTPError) as unauthenticated_ticket:
                        urlopen(
                            _json_request(
                                f"{base}/api/ws-ticket",
                                {},
                                public_headers,
                            ),
                            timeout=4,
                        )
                    self.addCleanup(unauthenticated_ticket.exception.close)
                    self.assertEqual(unauthenticated_ticket.exception.code, 401)

                    with self.assertRaises(HTTPError) as blocked_lobby_post:
                        urlopen(
                            _json_request(
                                f"{base}/api/lobby",
                                {"message": "host path from guest"},
                                public_headers,
                            ),
                            timeout=4,
                        )
                    self.addCleanup(blocked_lobby_post.exception.close)
                    self.assertEqual(blocked_lobby_post.exception.code, 403)

                    for blocked_path in ("/api/side-chat", "/api/room-friends/dm"):
                        with self.assertRaises(HTTPError) as blocked_private_post:
                            urlopen(
                                _json_request(
                                    f"{base}{blocked_path}",
                                    {"message": "private path from public guest"},
                                    public_headers,
                                ),
                                timeout=4,
                            )
                        self.addCleanup(blocked_private_post.exception.close)
                        self.assertEqual(blocked_private_post.exception.code, 403)

                    ticket, _headers = self._ws_ticket(
                        base,
                        str(session_payload["session_token"]),
                        public_headers,
                    )
                    room_client = connect_room_ws_with_ticket(
                        base,
                        ticket,
                        ["room_events"],
                    )
                    try:
                        request_id = room_client.command(
                            "message.send",
                            {"content": "hello from guest"},
                            request_id="public-message-1",
                        )
                        say_payload = self._room_command_result(room_client, request_id)
                    finally:
                        room_client.close()
                    event = dict(say_payload["result"]["event"])
                    self.assertEqual(event["content"], "hello from guest")
                    self.assertEqual(event["actor_id"], session_payload["agent_id"])
                    self.assertEqual(event["room_id"], "friend-room")
                    self.assertNotIn(session_payload["session_token"], json.dumps(say_payload))

                    with urlopen(
                        _json_request(
                            f"{base}/api/room-invite/create",
                            {
                                "meeting_id": "friend-room",
                                "display_name": "Read Only Friend",
                                "invite_scope": "read_only",
                            },
                            {"X-Host-Token": host_token},
                        ),
                        timeout=4,
                    ) as response:
                        read_only_invite = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(read_only_invite["invite_scope"], "read_only")

                    with urlopen(
                        _json_request(
                            f"{base}/api/room-invite/join",
                            {
                                "invite_token": read_only_invite["invite_token"],
                                "request_id": str(uuid4()),
                                "device_token": "read-only-public-device",
                            },
                            public_headers,
                        ),
                        timeout=4,
                    ) as response:
                        read_only_session = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(read_only_session["status"], "admitted")
                    self.assertEqual(read_only_session["invite_scope"], "read_only")

                    read_only_ticket, _headers = self._ws_ticket(
                        base,
                        str(read_only_session["session_token"]),
                        public_headers,
                    )
                    read_only_client = connect_room_ws_with_ticket(
                        base,
                        read_only_ticket,
                        ["room_events"],
                    )
                    try:
                        request_id = read_only_client.command(
                            "message.send",
                            {"content": "should not post"},
                            request_id="read-only-message-1",
                        )
                        rejected = self._room_command_result(read_only_client, request_id)
                    finally:
                        read_only_client.close()
                    self.assertEqual(rejected["op"], "nack")
                    self.assertEqual(rejected["error"]["code"], "permission_denied")

                    with self.assertRaises(HTTPError) as read_only_companion:
                        urlopen(
                            _json_request(
                                f"{base}/api/room-invite/companion",
                                {"agent_id": "blocked-ai", "display_name": "Blocked AI"},
                                {
                                    **public_headers,
                                    "Authorization": f"Bearer {read_only_session['session_token']}",
                                },
                            ),
                            timeout=4,
                        )
                    self.addCleanup(read_only_companion.exception.close)
                    self.assertEqual(read_only_companion.exception.code, 403)

                    with urlopen(
                        _json_request(
                            f"{base}/api/room-invite/companion",
                            {"agent_id": "friend-ai", "display_name": "Friend AI"},
                            {
                                **public_headers,
                                "Authorization": f"Bearer {session_payload['session_token']}",
                            },
                        ),
                        timeout=4,
                    ) as response:
                        companion = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(companion["meeting_id"], "friend-room")
                    self.assertNotEqual(companion["agent_id"], "friend-ai")
                    self.assertEqual(
                        companion["remote_client_packet"]["agent"]["agent_id"],
                        companion["agent_id"],
                    )
                    self.assertNotIn("env", companion["remote_client_packet"])
                    self.assertFalse(companion["remote_client_packet"]["safety"]["contains_invite_token"])
                finally:
                    server.shutdown()
                    server.server_close()

    def test_public_guest_invite_rejects_opaque_null_origin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            RoomStore(root).create_room("friend-room", label="Friend room")
            set_runtime_host_token("host-secret")
            set_runtime_public_url("https://shared-room.example.com")
            server = self._start_server(root)
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(
                    _json_request(
                        f"{base}/api/room-invite/create",
                        {"meeting_id": "friend-room", "display_name": "Friend"},
                        {"X-Host-Token": "host-secret"},
                    ),
                    timeout=4,
                ) as response:
                    invite = json.loads(response.read().decode("utf-8"))

                null_origin_headers = {
                    "Host": "shared-room.example.com",
                    "Origin": "null",
                }
                preflight = Request(
                    f"{base}/api/room-invite/join",
                    headers={
                        **null_origin_headers,
                        "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": "content-type",
                    },
                    method="OPTIONS",
                )
                with self.assertRaises(HTTPError) as blocked_preflight:
                    urlopen(preflight, timeout=4)
                self.addCleanup(blocked_preflight.exception.close)
                self.assertEqual(blocked_preflight.exception.code, 403)

                with self.assertRaises(HTTPError) as blocked_join:
                    urlopen(
                        _json_request(
                            f"{base}/api/room-invite/join",
                            {
                                "invite_token": invite["invite_token"],
                                "request_id": str(uuid4()),
                            },
                            null_origin_headers,
                        ),
                        timeout=4,
                    )
                self.addCleanup(blocked_join.exception.close)
                self.assertEqual(blocked_join.exception.code, 403)
            finally:
                server.shutdown()
                server.server_close()

    def test_gui_invite_session_store_survives_server_restart_without_raw_session_token(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            RoomStore(root).create_room("restart-room", label="Restart room")
            server = self._start_server(root)
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(
                    _json_request(
                        f"{base}/api/room-invite/create",
                        {
                            "meeting_id": "restart-room",
                            "agent_id": "guest-1",
                            "display_name": "Guest One",
                            "local_dev_preview": True,
                            "max_uses": 1,
                        },
                    ),
                    timeout=4,
                ) as response:
                    invite = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    _json_request(
                        f"{base}/api/room-invite/join",
                        {
                            "invite_token": invite["invite_token"],
                            "request_id": str(uuid4()),
                        },
                    ),
                    timeout=4,
                ) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            store_path = root / ".agentsassemble" / "room-invite-state.json"
            store_text = store_path.read_text(encoding="utf-8")
            self.assertNotIn(session_payload["session_token"], store_text)
            self.assertNotIn(invite["invite_token"], store_text)

            reset_state()
            restarted = self._start_server(root)
            try:
                restarted_base = f"http://127.0.0.1:{restarted.server_port}"
                ticket, _headers = self._ws_ticket(
                    restarted_base,
                    str(session_payload["session_token"]),
                )
                self.assertTrue(ticket.startswith("wst_"))

                with self.assertRaises(HTTPError) as reused_invite:
                    urlopen(
                        _json_request(
                            f"{restarted_base}/api/room-invite/join",
                            {
                                "invite_token": invite["invite_token"],
                                "request_id": str(uuid4()),
                            },
                        ),
                        timeout=4,
                    )
                self.addCleanup(reused_invite.exception.close)
                self.assertEqual(reused_invite.exception.code, 403)
            finally:
                restarted.shutdown()
                restarted.server_close()

    def test_public_guest_room_capability_and_preferences_survive_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            store = RoomStore(root)
            store.create_room("guest-room", label="Guest room")
            store.create_room("private-room", label="Private room")
            set_runtime_host_token("host-secret")
            set_runtime_public_url("https://shared-room.example.com")
            server = self._start_server(root)
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(
                    _json_request(
                        f"{base}/api/room-invite/create",
                        {
                            "meeting_id": "guest-room",
                            "display_name": "Friend",
                        },
                        {"X-Host-Token": "host-secret"},
                    ),
                    timeout=4,
                ) as response:
                    invite = json.loads(response.read().decode("utf-8"))
                public_headers = {
                    "Host": "shared-room.example.com",
                    "Origin": "https://shared-room.example.com",
                    "X-Device-Token": "persistent-guest-device",
                }
                with urlopen(
                    _json_request(
                        f"{base}/api/room-invite/join",
                        {
                            "invite_token": invite["invite_token"],
                            "request_id": str(uuid4()),
                            "device_token": "persistent-guest-device",
                        },
                        public_headers,
                    ),
                    timeout=4,
                ) as response:
                    admitted = json.loads(response.read().decode("utf-8"))
                session_headers = {
                    **public_headers,
                    "Authorization": f"Bearer {admitted['session_token']}",
                }

                with urlopen(
                    Request(f"{base}/api/rooms", headers=session_headers),
                    timeout=4,
                ) as response:
                    rooms = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    Request(
                        f"{base}/api/room-settings?room_id=guest-room",
                        headers=session_headers,
                    ),
                    timeout=4,
                ) as response:
                    own_settings = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    Request(
                        f"{base}/api/room-channels?meeting_id=guest-room",
                        headers=session_headers,
                    ),
                    timeout=4,
                ) as response:
                    own_channels = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    Request(
                        f"{base}/api/room-members?meeting_id=guest-room",
                        headers=session_headers,
                    ),
                    timeout=4,
                ) as response:
                    own_members = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    _json_request(
                        f"{base}/api/room-settings",
                        {
                            "room_id": "guest-room",
                            "appearance": {"notifications": "mute"},
                        },
                        session_headers,
                    ),
                    timeout=4,
                ) as response:
                    saved_preferences = json.loads(
                        response.read().decode("utf-8")
                    )
                with self.assertRaises(HTTPError) as global_write:
                    urlopen(
                        _json_request(
                            f"{base}/api/room-settings",
                            {
                                "room_id": "guest-room",
                                "label": "Guest overwrite",
                            },
                            session_headers,
                        ),
                        timeout=4,
                    )
                self.addCleanup(global_write.exception.close)
                cross_room_codes: list[int] = []
                for path in (
                    "/api/room-settings?room_id=private-room",
                    "/api/room-channels?meeting_id=private-room",
                    "/api/room-members?meeting_id=private-room",
                ):
                    with self.assertRaises(HTTPError) as cross_room:
                        urlopen(
                            Request(f"{base}{path}", headers=session_headers),
                            timeout=4,
                        )
                    self.addCleanup(cross_room.exception.close)
                    cross_room_codes.append(cross_room.exception.code)
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(
                [room["room_id"] for room in rooms["rooms"]],
                ["guest-room"],
            )
            self.assertEqual(
                own_settings["settings"]["room_id"],
                "guest-room",
            )
            self.assertEqual(own_channels["room_id"], "guest-room")
            self.assertEqual(own_members["meeting_id"], "guest-room")
            self.assertEqual(
                saved_preferences["settings"]["appearance"]["notifications"],
                "mute",
            )
            self.assertEqual(global_write.exception.code, 403)
            self.assertEqual(cross_room_codes, [403, 403, 403])

            reset_state()
            set_runtime_host_token("host-secret")
            set_runtime_public_url("https://shared-room.example.com")
            restarted = self._start_server(root)
            try:
                restarted_base = (
                    f"http://127.0.0.1:{restarted.server_port}"
                )
                with urlopen(
                    Request(
                        f"{restarted_base}/api/room-settings"
                        "?room_id=guest-room",
                        headers=session_headers,
                    ),
                    timeout=4,
                ) as response:
                    restored_settings = json.loads(
                        response.read().decode("utf-8")
                    )
                with urlopen(
                    Request(
                        f"{restarted_base}/api/rooms",
                        headers=session_headers,
                    ),
                    timeout=4,
                ) as response:
                    restored_rooms = json.loads(response.read().decode("utf-8"))
            finally:
                restarted.shutdown()
                restarted.server_close()

        self.assertEqual(
            restored_settings["settings"]["appearance"]["notifications"],
            "mute",
        )
        self.assertEqual(
            restored_settings["settings"]["label"],
            "Guest room",
        )
        self.assertEqual(
            [room["room_id"] for room in restored_rooms["rooms"]],
            ["guest-room"],
        )

    def test_guest_companion_packet_uses_session_room_and_does_not_reflect_guest_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            RoomStore(root).create_room("friend-room", label="Friend room")
            server = self._start_server(root)
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(
                    _json_request(
                        f"{base}/api/room-invite/create",
                        {
                            "meeting_id": "friend-room",
                            "agent_id": "friend",
                            "display_name": "Friend",
                            "local_dev_preview": True,
                        },
                    ),
                    timeout=4,
                ) as response:
                    invite = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    _json_request(
                        f"{base}/api/room-invite/join",
                        {
                            "invite_token": invite["invite_token"],
                            "request_id": str(uuid4()),
                            "device_token": "companion-packet-device",
                        },
                    ),
                    timeout=4,
                ) as response:
                    session_payload = json.loads(response.read().decode("utf-8"))

                host_token = "fake-host-token-that-must-not-leak"
                provider_secret = "fake-provider-secret-that-must-not-leak"
                filesystem_path = "/tmp/agentsassemble-private/provider-config.json"
                with urlopen(
                    _json_request(
                        f"{base}/api/room-invite/companion",
                        {
                            "meeting_id": "evil-room",
                            "room_url": f"http://127.0.0.1:{server.server_port}{filesystem_path}",
                            "agent_id": "friend-ai",
                            "display_name": "Friend AI",
                            "session_token": session_payload["session_token"],
                            "host_token": host_token,
                            "provider_secret": provider_secret,
                            "filesystem_path": filesystem_path,
                        },
                        {"Authorization": f"Bearer {session_payload['session_token']}"},
                    ),
                    timeout=4,
                ) as response:
                    companion = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            packet = companion["remote_client_packet"]
            serialized = json.dumps(companion, ensure_ascii=False)
            self.assertEqual(companion["meeting_id"], "friend-room")
            self.assertEqual(packet["agent"]["meeting_id"], "friend-room")
            self.assertNotIn("env", packet)
            self.assertNotIn("http", packet)
            self.assertEqual(packet["attend"]["invite_input"], "hidden_stdin")
            self.assertEqual(packet["admission_contract"]["provider_execution"], "not_started_by_invite")
            self.assertFalse(packet["safety"]["contains_session_token"])
            self.assertFalse(packet["safety"]["provider_executed"])
            self.assertFalse(packet["safety"]["host_filesystem_granted"])
            self.assertNotIn(session_payload["session_token"], serialized)
            self.assertNotIn(host_token, serialized)
            self.assertNotIn(provider_secret, serialized)
            self.assertNotIn(filesystem_path, serialized)

    def test_tunnel_start_reports_missing_cloudflared_without_exposing_token(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENTSASSEMBLE_HOST_TOKEN", None)
            os.environ.pop("AGENTSASSEMBLE_PUBLIC_URL", None)
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                runtime = PublicInviteRuntime(environ={})
                tunnel = PublicTunnelManager(
                    public_invite_runtime=runtime,
                    which=lambda _name: None,
                )
                server = ThreadingHTTPServer(
                    ("127.0.0.1", 0),
                    _make_handler(
                        root,
                        public_tunnel_manager=tunnel,
                        public_invite_runtime_override=runtime,
                    ),
                )
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{server.server_port}"
                    with urlopen(_json_request(f"{base}/api/public-invite/host-token", {}), timeout=4) as response:
                        token_payload = json.loads(response.read().decode("utf-8"))
                    host_token = token_payload["host_token"]

                    with urlopen(
                        _json_request(
                            f"{base}/api/public-invite/tunnel/start",
                            {},
                            {"X-Host-Token": host_token},
                        ),
                        timeout=4,
                    ) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    public_invite = payload["public_invite"]
                    self.assertFalse(public_invite["tunnel"]["available"])
                    self.assertEqual(public_invite["tunnel"]["last_error"], "cloudflared is not installed")
                    self.assertNotIn("host_token", public_invite)

                    with urlopen(f"{base}/api/public-invite/status", timeout=4) as response:
                        status = json.loads(response.read().decode("utf-8"))
                    self.assertTrue(status["host_token_configured"])
                    self.assertNotIn("host_token", status)
                finally:
                    server.shutdown()
                    server.server_close()

    def test_existing_env_host_token_is_not_returned_by_bootstrap_endpoint(self):
        with patch.dict(os.environ, {"AGENTSASSEMBLE_HOST_TOKEN": "env-secret"}, clear=True):
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{server.server_port}"
                    with self.assertRaises(HTTPError) as unauthenticated:
                        urlopen(_json_request(f"{base}/api/public-invite/host-token", {}), timeout=4)
                    self.addCleanup(unauthenticated.exception.close)
                    self.assertEqual(unauthenticated.exception.code, 403)

                    with urlopen(
                        _json_request(
                            f"{base}/api/public-invite/host-token",
                            {},
                            {"X-Host-Token": "env-secret"},
                        ),
                        timeout=4,
                    ) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(payload["status"], "already_configured")
                    self.assertNotIn("host_token", payload)
                finally:
                    server.shutdown()
                    server.server_close()

    def test_runtime_host_token_can_be_regenerated_from_local_operator_ui(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENTSASSEMBLE_HOST_TOKEN", None)
            os.environ.pop("AGENTSASSEMBLE_PUBLIC_URL", None)
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                set_runtime_host_token("lost-runtime-token")
                server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{server.server_port}"
                    with urlopen(_json_request(f"{base}/api/public-invite/host-token", {}), timeout=4) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(payload["status"], "regenerated")
                    self.assertTrue(payload["host_token"])
                    self.assertNotEqual(payload["host_token"], "lost-runtime-token")
                    self.assertTrue(payload["public_invite"]["host_token_configured"])
                finally:
                    server.shutdown()
                    server.server_close()


if __name__ == "__main__":
    unittest.main()
