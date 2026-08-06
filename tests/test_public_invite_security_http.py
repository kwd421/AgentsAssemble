from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from agentsassemble.admission.invite import (
    reset_state,
    set_runtime_host_token,
    set_runtime_public_url,
)
from agentsassemble.gui import _make_handler
from agentsassemble.persistence.local.room.repository import RoomStore


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
