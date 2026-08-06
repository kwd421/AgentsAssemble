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

from agentsassemble.application.public_invite_runtime import PublicInviteRuntime
from agentsassemble.application.public_tunnel import PublicTunnelManager
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


class PublicInviteLifecycleHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "room"
        self.store = RoomStore(self.root)
        self.store.create_room("friend-room", label="Friend room")
        self.runtime = PublicInviteRuntime(environ={})
        self.runtime.set_host_token("host-secret")
        self.runtime.set_public_url("https://shared-room.example.com")
        tunnel = PublicTunnelManager(
            public_invite_runtime=self.runtime,
            which=lambda _name: None,
        )
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            _make_handler(
                self.root,
                public_tunnel_manager=tunnel,
                public_invite_runtime_override=self.runtime,
            ),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.public_headers = {
            "Host": "shared-room.example.com",
            "Origin": "https://shared-room.example.com",
            "X-Forwarded-Proto": "https",
        }

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.store.close()
        self.temporary_directory.cleanup()

    def test_manual_public_url_can_be_cleared_and_external_invites_stop(self) -> None:
        with urlopen(
            _json_request(
                f"{self.base}/api/public-invite/public-url",
                {"public_url": ""},
                {"X-Host-Token": "host-secret"},
            ),
            timeout=4,
        ) as response:
            cleared = json.loads(response.read().decode("utf-8"))

        with self.assertRaises(HTTPError) as invite_error:
            urlopen(
                _json_request(
                    f"{self.base}/api/room-invite/create",
                    {"meeting_id": "friend-room", "display_name": "Friend"},
                    {"X-Host-Token": "host-secret"},
                ),
                timeout=4,
            )
        invite_error.exception.close()

        self.assertEqual(cleared["status"], "cleared")
        self.assertEqual(cleared["public_url"], "")
        self.assertEqual(cleared["public_invite"]["public_url"], "")
        self.assertEqual(invite_error.exception.code, 409)

    def test_guest_leave_persists_left_state_and_revokes_room_access(self) -> None:
        with urlopen(
            _json_request(
                f"{self.base}/api/room-invite/create",
                {"meeting_id": "friend-room", "display_name": "Friend"},
                {"X-Host-Token": "host-secret"},
            ),
            timeout=4,
        ) as response:
            invite = json.loads(response.read().decode("utf-8"))
        with urlopen(
            _json_request(
                f"{self.base}/api/room-invite/join",
                {
                    "invite_token": invite["invite_token"],
                    "request_id": str(uuid4()),
                    "display_name": "Guest Sync QA",
                },
                self.public_headers,
            ),
            timeout=4,
        ) as response:
            session = json.loads(response.read().decode("utf-8"))
        with urlopen(
            _json_request(
                f"{self.base}/api/room-invite/leave",
                {},
                {
                    **self.public_headers,
                    "Authorization": f"Bearer {session['session_token']}",
                },
            ),
            timeout=4,
        ) as response:
            left = json.loads(response.read().decode("utf-8"))

        participant = self.store.participant(
            "friend-room",
            str(session["agent_id"]),
        )
        leave_events = [
            event
            for event in self.store.read_events("friend-room")
            if event["type"] == "participant_left"
            and event.get("participant_id") == session["agent_id"]
        ]
        with self.assertRaises(HTTPError) as ticket_error:
            urlopen(
                _json_request(
                    f"{self.base}/api/ws-ticket",
                    {},
                    {
                        **self.public_headers,
                        "Authorization": f"Bearer {session['session_token']}",
                    },
                ),
                timeout=4,
            )
        ticket_error.exception.close()

        self.assertEqual(left, {"status": "left", "agent_id": session["agent_id"]})
        self.assertEqual(participant["status"], "left")
        self.assertEqual(len(leave_events), 1)
        self.assertEqual(ticket_error.exception.code, 401)

    def test_invited_guest_profile_updates_the_canonical_room_identity(self) -> None:
        with urlopen(
            _json_request(
                f"{self.base}/api/room-invite/create",
                {"meeting_id": "friend-room", "display_name": "Friend"},
                {"X-Host-Token": "host-secret"},
            ),
            timeout=4,
        ) as response:
            invite = json.loads(response.read().decode("utf-8"))
        with urlopen(
            _json_request(
                f"{self.base}/api/room-invite/join",
                {
                    "invite_token": invite["invite_token"],
                    "request_id": str(uuid4()),
                    "display_name": "Guest Before",
                    "device_token": "guest-profile-device-token",
                },
                self.public_headers,
            ),
            timeout=4,
        ) as response:
            session = json.loads(response.read().decode("utf-8"))

        guest_headers = {
            **self.public_headers,
            "Authorization": f"Bearer {session['session_token']}",
        }
        with urlopen(
            _json_request(
                f"{self.base}/api/user-profile",
                {
                    "display_name": "Guest After",
                    "avatar_label": "GA",
                    "custom_status": "초대 게스트 프로필",
                },
                guest_headers,
            ),
            timeout=4,
        ) as response:
            saved = json.loads(response.read().decode("utf-8"))
        with urlopen(
            Request(f"{self.base}/api/user-profile", headers=guest_headers),
            timeout=4,
        ) as response:
            loaded = json.loads(response.read().decode("utf-8"))

        participant = self.store.participant("friend-room", str(session["agent_id"]))
        profile_events = [
            event
            for event in self.store.read_events("friend-room")
            if event["type"] == "participant_updated"
            and event.get("participant_id") == session["agent_id"]
        ]

        self.assertEqual(saved["profile"]["display_name"], "Guest After")
        self.assertEqual(loaded["profile"]["custom_status"], "초대 게스트 프로필")
        self.assertEqual(participant["display_name"], "Guest After")
        self.assertEqual(profile_events[-1]["display_name"], "Guest After")

    def test_guest_recovery_moves_the_same_identity_to_a_new_device_once(self) -> None:
        with urlopen(
            _json_request(
                f"{self.base}/api/room-invite/create",
                {"meeting_id": "friend-room", "display_name": "Friend"},
                {"X-Host-Token": "host-secret"},
            ),
            timeout=4,
        ) as response:
            invite = json.loads(response.read().decode("utf-8"))
        with urlopen(
            _json_request(
                f"{self.base}/api/room-invite/join",
                {
                    "invite_token": invite["invite_token"],
                    "request_id": str(uuid4()),
                    "display_name": "Recoverable Guest",
                    "device_token": "original-recovery-device",
                    "client_id": "original-client",
                },
                self.public_headers,
            ),
            timeout=4,
        ) as response:
            original = json.loads(response.read().decode("utf-8"))
        session_headers = {
            **self.public_headers,
            "Authorization": f"Bearer {original['session_token']}",
        }
        with urlopen(
            _json_request(
                f"{self.base}/api/identity/recovery-code",
                {"room_id": "friend-room"},
                session_headers,
            ),
            timeout=4,
        ) as response:
            issued = json.loads(response.read().decode("utf-8"))
        self.assertTrue(issued["recovery_url"].startswith("https://shared-room.example.com/"))
        with urlopen(
            _json_request(
                f"{self.base}/api/identity/recovery-code/redeem",
                {
                    "recovery_code": issued["recovery_code"],
                    "room_id": "friend-room",
                    "device_token": "replacement-recovery-device",
                    "client_id": "replacement-client",
                },
                self.public_headers,
            ),
            timeout=4,
        ) as response:
            recovered = json.loads(response.read().decode("utf-8"))

        with self.assertRaises(HTTPError) as reused_error:
            urlopen(
                _json_request(
                    f"{self.base}/api/identity/recovery-code/redeem",
                    {
                        "recovery_code": issued["recovery_code"],
                        "room_id": "friend-room",
                        "device_token": "third-recovery-device",
                        "client_id": "third-client",
                    },
                    self.public_headers,
                ),
                timeout=4,
            )
        reused_error.exception.close()

        self.assertEqual(recovered["agent_id"], original["agent_id"])
        self.assertNotEqual(recovered["session_token"], original["session_token"])
        self.assertEqual(recovered["client_id"], "replacement-client")
        self.assertNotEqual(recovered["recovery_code"], issued["recovery_code"])
        self.assertEqual(reused_error.exception.code, 403)
        raw_code = issued["recovery_code"].encode("utf-8")
        self.assertFalse(
            any(
                raw_code in path.read_bytes()
                for path in self.root.rglob("identity.db*")
                if path.is_file()
            )
        )

    def test_recovery_rate_limit_keeps_distinct_forwarded_clients_independent(self) -> None:
        endpoint = f"{self.base}/api/identity/recovery-code/redeem"
        payload = {
            "recovery_code": "invalid-recovery-code",
            "room_id": "friend-room",
            "device_token": "replacement-device",
            "client_id": "replacement-client",
        }
        for _ in range(8):
            with self.assertRaises(HTTPError) as rejected:
                urlopen(
                    _json_request(
                        endpoint,
                        payload,
                        {**self.public_headers, "X-Forwarded-For": "198.51.100.10"},
                    ),
                    timeout=4,
                )
            rejected.exception.close()
            self.assertEqual(rejected.exception.code, 403)

        with self.assertRaises(HTTPError) as other_client:
            urlopen(
                _json_request(
                    endpoint,
                    {
                        **payload,
                        "recovery_code": "different-invalid-recovery-code",
                        "client_id": "other-client",
                    },
                    {**self.public_headers, "X-Forwarded-For": "198.51.100.11"},
                ),
                timeout=4,
            )
        other_client.exception.close()

        self.assertEqual(other_client.exception.code, 403)
