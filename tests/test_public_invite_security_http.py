from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from agentsassemble.admission.invite import (
    reset_state,
    set_runtime_host_token,
    set_runtime_public_url,
)
from agentsassemble.gui import _make_handler
from agentsassemble.persistence.local.room.repository import RoomStore
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
        reset_state()

    def tearDown(self) -> None:
        reset_state()

    def _start_server(self, root: Path) -> ThreadingHTTPServer:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
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

    def test_http_responses_deny_embedding_in_an_external_frame(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            RoomStore(root).create_room("friend-room", label="Friend room")
            set_runtime_host_token("host-secret")
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

    def test_public_room_request_cannot_reach_unclassified_compatibility_mutation(self):
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
                                "message": "must not reach compatibility storage",
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

        self.assertEqual(rejected.exception.code, 403)
        self.assertEqual(payload.get("code"), "local_operator_required")
        self.assertFalse(event_written)

    def test_guest_companion_cannot_replace_operator_identity_or_gain_moderation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            RoomStore(root).create_room("friend-room", label="Friend room")
            set_runtime_host_token("host-secret")
            set_runtime_public_url("https://shared-room.example.com")
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

    def test_invite_admission_rejects_an_existing_identity_owned_by_another_principal(self):
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
