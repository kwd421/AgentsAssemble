from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from agentsassemble.application.public_invite_runtime import PublicInviteRuntime
from agentsassemble.gui import _make_handler
from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.web.room_client import connect_room_ws
from agentsassemble.web.router import GuiDeps, RequestContext


def _json_request(
    url: str,
    payload: dict[str, object],
    headers: dict[str, str] | None = None,
) -> Request:
    return Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )


class PublicInviteSecurityHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.public_invite = PublicInviteRuntime(environ={})

    def tearDown(self) -> None:
        self.public_invite.clear_public_url()

    def _start_server(self, root: Path) -> ThreadingHTTPServer:
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            _make_handler(root, public_invite_runtime_override=self.public_invite),
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def test_external_peer_cannot_claim_local_operator_trust_with_spoofed_headers(self):
        handler = SimpleNamespace(
            headers={
                "Host": "127.0.0.1:8765",
                "Origin": "http://127.0.0.1:8765",
            },
            server=SimpleNamespace(server_address=("127.0.0.1", 8765)),
            client_address=("198.51.100.17", 43123),
        )
        context = RequestContext(
            handler,
            GuiDeps(output_root=Path(".")),
            urlparse("/api/provider-credentials/deepseek"),
            {},
        )

        self.assertFalse(context.is_local_operator())

    def test_forwarded_loopback_request_cannot_bootstrap_a_host_token(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = self._start_server(Path(temp_dir) / "room")
            try:
                request = _json_request(
                    f"http://127.0.0.1:{server.server_port}/api/public-invite/host-token",
                    {},
                    {
                        "Host": f"127.0.0.1:{server.server_port}",
                        "X-Forwarded-For": "203.0.113.40",
                        "X-Forwarded-Proto": "https",
                    },
                )
                with self.assertRaises(HTTPError) as rejected:
                    urlopen(request, timeout=4)
                payload = json.loads(rejected.exception.read().decode("utf-8"))
                rejected.exception.close()
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(rejected.exception.code, HTTPStatus.FORBIDDEN)
        self.assertEqual(payload.get("code"), "local_operator_required")

    def test_http_responses_deny_embedding_in_an_external_frame(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            RoomStore(root).create_room("friend-room", label="Friend room")
            self.public_invite.set_host_token("host-secret")
            server = self._start_server(root)
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(
                    _json_request(
                        f"{base}/api/host/claim",
                        {"device_token": "operator-device-token"},
                        {"X-Host-Token": "host-secret"},
                    ),
                    timeout=4,
                ) as response:
                    self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
                    self.assertIn(
                        "frame-ancestors 'none'",
                        response.headers.get("Content-Security-Policy", ""),
                    )
            finally:
                server.shutdown()
                server.server_close()

    def test_first_party_plugin_web_asset_allows_only_same_origin_embedding(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            RoomStore(root).create_room("plugin-room", label="Plugin room")
            server = self._start_server(root)
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(
                    f"{base}/plugins/rimworld/web/index.html",
                    timeout=4,
                ) as response:
                    self.assertEqual(
                        response.headers.get("X-Frame-Options"),
                        "SAMEORIGIN",
                    )
                    self.assertIn(
                        "frame-ancestors 'self'",
                        response.headers.get("Content-Security-Policy", ""),
                    )
            finally:
                server.shutdown()
                server.server_close()

    def test_unauthenticated_lan_caller_cannot_write_human_side_chat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            server = ThreadingHTTPServer(("0.0.0.0", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with self.assertRaises(HTTPError) as rejected:
                    urlopen(
                        _json_request(
                            f"{base}/api/side-chat",
                            {
                                "name": "remote caller",
                                "message": "must not reach side chat",
                                "flow_meeting_id": "room-a",
                            },
                            {
                                "Host": "lan-room.example.com",
                                "Origin": "http://lan-room.example.com",
                            },
                        ),
                        timeout=4,
                    )
                payload = json.loads(rejected.exception.read().decode("utf-8"))
                rejected.exception.close()
                event_written = (root / "side_chat.jsonl").exists()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(rejected.exception.code, 401)
        self.assertEqual(payload.get("error"), "session token required")
        self.assertFalse(event_written)

    def test_guest_companion_cannot_replace_operator_identity_or_gain_moderation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            RoomStore(root).create_room("friend-room", label="Friend room")
            self.public_invite.set_host_token("host-secret")
            self.public_invite.set_public_url("https://shared-room.example.com")
            server = self._start_server(root)
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                public_headers = {
                    "Host": "shared-room.example.com",
                    "Origin": "https://shared-room.example.com",
                }
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
                        f"{base}/api/room-invite/join",
                        {
                            "invite_token": invite["invite_token"],
                            "request_id": str(uuid4()),
                            "device_token": "ordinary-guest-device",
                        },
                        public_headers,
                    ),
                    timeout=4,
                ) as response:
                    guest = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    _json_request(
                        f"{base}/api/room-invite/companion",
                        {
                            "agent_id": "operator-local",
                            "display_name": "Impersonated operator",
                        },
                        {
                            **public_headers,
                            "Authorization": f"Bearer {guest['session_token']}",
                        },
                    ),
                    timeout=4,
                ) as response:
                    companion_invite = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    _json_request(
                        f"{base}/api/room-invite/join",
                        {
                            "invite_token": companion_invite["invite_token"],
                            "request_id": str(uuid4()),
                        },
                        public_headers,
                    ),
                    timeout=4,
                ) as response:
                    companion = json.loads(response.read().decode("utf-8"))

                with self.assertRaises(HTTPError) as recursive_invite_error:
                    urlopen(
                        _json_request(
                            f"{base}/api/room-invite/companion",
                            {"display_name": "Recursive companion"},
                            {
                                **public_headers,
                                "Authorization": f"Bearer {companion['session_token']}",
                            },
                        ),
                        timeout=4,
                    )
                recursive_payload = json.loads(
                    recursive_invite_error.exception.read().decode("utf-8")
                )
                recursive_invite_error.exception.close()

                for index in range(7):
                    with urlopen(
                        _json_request(
                            f"{base}/api/room-invite/companion",
                            {"display_name": f"Bounded companion {index}"},
                            {
                                **public_headers,
                                "Authorization": f"Bearer {guest['session_token']}",
                            },
                        ),
                        timeout=4,
                    ):
                        pass
                with self.assertRaises(HTTPError) as companion_limit_error:
                    urlopen(
                        _json_request(
                            f"{base}/api/room-invite/companion",
                            {"display_name": "One companion too many"},
                            {
                                **public_headers,
                                "Authorization": f"Bearer {guest['session_token']}",
                            },
                        ),
                        timeout=4,
                    )
                limit_payload = json.loads(
                    companion_limit_error.exception.read().decode("utf-8")
                )
                companion_limit_error.exception.close()

                with self.assertRaises(HTTPError) as moderation_error:
                    urlopen(
                        Request(
                            f"{base}/api/room-invite/invites",
                            headers={
                                **public_headers,
                                "Authorization": f"Bearer {companion['session_token']}",
                            },
                        ),
                        timeout=4,
                    )
                self.addCleanup(moderation_error.exception.close)
            finally:
                server.shutdown()
                server.server_close()

        self.assertNotEqual(companion["agent_id"], "operator-local")
        self.assertEqual(moderation_error.exception.code, 403)
        self.assertEqual(recursive_invite_error.exception.code, 403)
        self.assertEqual(recursive_payload.get("code"), "companion_owner_required")
        self.assertEqual(companion_limit_error.exception.code, 429)
        self.assertEqual(limit_payload.get("code"), "companion_limit_reached")

    def test_reusable_invite_without_stable_identity_cannot_partition_companion_quota(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            RoomStore(root).create_room("friend-room", label="Friend room")
            self.public_invite.set_host_token("host-secret")
            self.public_invite.set_public_url("https://shared-room.example.com")
            server = self._start_server(root)
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                public_headers = {
                    "Host": "shared-room.example.com",
                    "Origin": "https://shared-room.example.com",
                }
                with urlopen(
                    _json_request(
                        f"{base}/api/room-invite/create",
                        {
                            "meeting_id": "friend-room",
                            "display_name": "Reusable guest",
                            "max_uses": 0,
                        },
                        {"X-Host-Token": "host-secret"},
                    ),
                    timeout=4,
                ) as response:
                    invite = json.loads(response.read().decode("utf-8"))
                with self.assertRaises(HTTPError) as rejected:
                    urlopen(
                        _json_request(
                            f"{base}/api/room-invite/join",
                            {
                                "invite_token": invite["invite_token"],
                                "request_id": str(uuid4()),
                            },
                            public_headers,
                        ),
                        timeout=4,
                    )
                payload = json.loads(rejected.exception.read().decode("utf-8"))
                rejected.exception.close()
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(rejected.exception.code, HTTPStatus.FORBIDDEN)
        self.assertEqual(payload.get("code"), "stable_device_required")

    def test_http_kick_revokes_the_live_session_and_disconnects_its_room_socket(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            store = RoomStore(root)
            store.create_room("friend-room", label="Friend room")
            store.close()
            self.public_invite.set_host_token("host-secret")
            self.public_invite.set_public_url("https://shared-room.example.com")
            server = self._start_server(root)
            room_client = None
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                public_headers = {
                    "Host": "shared-room.example.com",
                    "Origin": "https://shared-room.example.com",
                }
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
                        f"{base}/api/room-invite/join",
                        {
                            "invite_token": invite["invite_token"],
                            "request_id": str(uuid4()),
                            "device_token": "kick-guest-device-token",
                        },
                        public_headers,
                    ),
                    timeout=4,
                ) as response:
                    guest = json.loads(response.read().decode("utf-8"))

                room_client = connect_room_ws(
                    base,
                    str(guest["session_token"]),
                    ["room_events"],
                    timeout=2,
                )
                room_client.set_receive_timeout(0.25)
                with urlopen(
                    _json_request(
                        f"{base}/api/room-participants/kick",
                        {
                            "room_id": "friend-room",
                            "participant_id": guest["agent_id"],
                        },
                        {"X-Host-Token": "host-secret"},
                    ),
                    timeout=4,
                ) as response:
                    kicked = json.loads(response.read().decode("utf-8"))

                with self.assertRaises(HTTPError) as revoked_ticket:
                    urlopen(
                        _json_request(
                            f"{base}/api/ws-ticket",
                            {},
                            {
                                **public_headers,
                                "Authorization": f"Bearer {guest['session_token']}",
                            },
                        ),
                        timeout=4,
                    )
                revoked_ticket.exception.close()
                for _ in range(20):
                    room_client.receive()
                    if room_client.closed:
                        break
            finally:
                if room_client is not None:
                    room_client.close()
                server.shutdown()
                server.server_close()

            persisted = RoomStore(root)
            try:
                participant = persisted.participant("friend-room", str(guest["agent_id"]))
            finally:
                persisted.close()

        self.assertEqual(kicked["status"], "kicked")
        self.assertEqual(participant["status"], "kicked")
        self.assertEqual(revoked_ticket.exception.code, HTTPStatus.UNAUTHORIZED)
        self.assertTrue(room_client.closed)

    def test_invite_admission_rejects_an_existing_identity_owned_by_another_principal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            RoomStore(root).create_room("friend-room", label="Friend room")
            self.public_invite.set_host_token("host-secret")
            self.public_invite.set_public_url("https://shared-room.example.com")
            server = self._start_server(root)
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(
                    _json_request(
                        f"{base}/api/host/claim",
                        {"device_token": "operator-device-token"},
                        {"X-Host-Token": "host-secret"},
                    ),
                    timeout=4,
                ):
                    pass
                with urlopen(
                    _json_request(
                        f"{base}/api/room-invite/create",
                        {
                            "meeting_id": "friend-room",
                            "agent_id": "operator-local",
                            "display_name": "Conflicting guest",
                            "max_uses": 1,
                        },
                        {"X-Host-Token": "host-secret"},
                    ),
                    timeout=4,
                ) as response:
                    invite = json.loads(response.read().decode("utf-8"))

                with self.assertRaises(HTTPError) as admission_error:
                    urlopen(
                        _json_request(
                            f"{base}/api/room-invite/join",
                            {
                                "invite_token": invite["invite_token"],
                                "request_id": str(uuid4()),
                            },
                            {
                                "Host": "shared-room.example.com",
                                "Origin": "https://shared-room.example.com",
                            },
                        ),
                        timeout=4,
                    )
                self.addCleanup(admission_error.exception.close)
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(admission_error.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
