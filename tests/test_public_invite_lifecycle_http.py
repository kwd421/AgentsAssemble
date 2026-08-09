from __future__ import annotations

import base64
import json
import tempfile
import threading
import unittest
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from agentsassemble.application.public_invite_runtime import PublicInviteRuntime
from agentsassemble.application.public_tunnel import PublicTunnelManager
from agentsassemble.gui import _make_handler
from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.room.attachments import read_attachment_metadata


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
        origin_host = self.runtime.prepare_managed_ingress(ingress_kind="cloudflare")
        self.runtime.set_managed_public_url(
            "https://shared-room.example.com",
            ingress_kind="cloudflare",
            expected_origin_host=origin_host,
        )
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
            "Host": self.runtime.managed_ingress_origin_host(),
            "X-Forwarded-Host": "shared-room.example.com",
            "Origin": "https://shared-room.example.com",
            "X-Forwarded-Proto": "https",
            "CF-Ray": "managed-ingress-test-ray",
        }

    def test_managed_cloudflare_headers_without_the_origin_credential_are_rejected(self) -> None:
        with self.assertRaises(HTTPError) as rejected:
            urlopen(
                _json_request(
                    f"{self.base}/api/room-invite/join",
                    {},
                    {
                        **self.public_headers,
                        "Host": "shared-room.example.com",
                    },
                ),
                timeout=4,
            )
        rejected.exception.close()

        self.assertEqual(rejected.exception.code, HTTPStatus.FORBIDDEN)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.store.close()
        self.temporary_directory.cleanup()

    def test_active_public_url_can_be_cleared_and_external_invites_stop(self) -> None:
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
                    "device_token": "guest-sync-device-token",
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

    def test_closing_room_revokes_existing_sessions_and_unused_invites(self) -> None:
        with urlopen(
            _json_request(
                f"{self.base}/api/room-invite/create",
                {
                    "meeting_id": "friend-room",
                    "display_name": "Closing room guest",
                    "max_uses": 2,
                },
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
                    "display_name": "Connected before close",
                    "device_token": "closing-room-device-token",
                },
                self.public_headers,
            ),
            timeout=4,
        ) as response:
            session = json.loads(response.read().decode("utf-8"))

        with urlopen(
            _json_request(
                f"{self.base}/api/rooms/close",
                {"room_id": "friend-room"},
                {"X-Host-Token": "host-secret"},
            ),
            timeout=4,
        ) as response:
            closed = json.loads(response.read().decode("utf-8"))

        with self.assertRaises(HTTPError) as stale_session:
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
        stale_session.exception.close()
        with self.assertRaises(HTTPError) as stale_invite:
            urlopen(
                _json_request(
                    f"{self.base}/api/room-invite/join",
                    {
                        "invite_token": invite["invite_token"],
                        "request_id": str(uuid4()),
                        "display_name": "Joined after close",
                    },
                    self.public_headers,
                ),
                timeout=4,
            )
        stale_invite.exception.close()
        with self.assertRaises(HTTPError) as reopen_closed_room:
            urlopen(
                _json_request(
                    f"{self.base}/api/rooms/archive",
                    {"room_id": "friend-room", "archived": False},
                    {"X-Host-Token": "host-secret"},
                ),
                timeout=4,
            )
        reopen_closed_room.exception.close()

        self.assertEqual(closed["status"], "closed")
        self.assertEqual(self.store.room("friend-room")["status"], "closed")
        self.assertEqual(stale_session.exception.code, HTTPStatus.UNAUTHORIZED)
        self.assertEqual(stale_invite.exception.code, HTTPStatus.FORBIDDEN)
        self.assertEqual(reopen_closed_room.exception.code, HTTPStatus.CONFLICT)

    def test_exporting_participant_revokes_its_existing_room_access(self) -> None:
        with urlopen(
            _json_request(
                f"{self.base}/api/room-invite/create",
                {"meeting_id": "friend-room", "display_name": "Exported guest"},
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
                    "display_name": "Exported guest",
                    "device_token": "exported-guest-device-token",
                },
                self.public_headers,
            ),
            timeout=4,
        ) as response:
            session = json.loads(response.read().decode("utf-8"))

        with urlopen(
            _json_request(
                f"{self.base}/api/room-participants/export",
                {
                    "room_id": "friend-room",
                    "participant_id": session["agent_id"],
                },
                {"X-Host-Token": "host-secret"},
            ),
            timeout=4,
        ) as response:
            exported = json.loads(response.read().decode("utf-8"))

        with self.assertRaises(HTTPError) as stale_session:
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
        stale_session.exception.close()

        participant = self.store.participant(
            "friend-room",
            str(session["agent_id"]),
        )
        self.assertEqual(exported["status"], "exported")
        self.assertEqual(participant["status"], "exported")
        self.assertEqual(stale_session.exception.code, HTTPStatus.UNAUTHORIZED)

    def test_invited_guest_profile_updates_the_canonical_room_identity(self) -> None:
        device_token = "guest-profile-device-token"
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
                f"{self.base}/api/attachments",
                {
                    "purpose": "profile_avatar",
                    "invite_token": invite["invite_token"],
                    "device_token": device_token,
                    "filename": "guest.png",
                    "content_type": "image/png",
                    "data_base64": base64.b64encode(b"guest-avatar").decode("ascii"),
                },
                self.public_headers,
            ),
            timeout=4,
        ) as response:
            avatar = json.loads(response.read().decode("utf-8"))["attachment"]
        with urlopen(
            _json_request(
                f"{self.base}/api/room-invite/join",
                {
                    "invite_token": invite["invite_token"],
                    "request_id": str(uuid4()),
                    "display_name": "Guest Before",
                    "avatar_image_url": avatar["url"],
                    "device_token": device_token,
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
        self.assertEqual(session["avatar_image_url"], avatar["url"])
        self.assertFalse(
            read_attachment_metadata(self.root, str(avatar["id"]))["prejoin_pending"]
        )
        self.assertEqual(participant["avatar_image_url"], avatar["url"])
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
                        {**self.public_headers, "CF-Connecting-IP": "198.51.100.10"},
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
                    {**self.public_headers, "CF-Connecting-IP": "198.51.100.11"},
                ),
                timeout=4,
            )
        other_client.exception.close()

        self.assertEqual(other_client.exception.code, 403)

    def test_recovery_rejects_forged_forwarding_headers_as_one_network(self) -> None:
        endpoint = f"{self.base}/api/identity/recovery-code/redeem"
        statuses: list[int] = []
        for index in range(20):
            with self.assertRaises(HTTPError) as rejected:
                urlopen(
                    _json_request(
                        endpoint,
                        {
                            "recovery_code": f"invalid-network-code-{index}",
                            "room_id": "friend-room",
                            "device_token": "forged-network-device",
                            "client_id": f"forged-network-client-{index}",
                        },
                        {
                            **self.public_headers,
                            "X-Forwarded-For": f"198.51.100.{index + 30}",
                        },
                    ),
                    timeout=4,
                )
            statuses.append(rejected.exception.code)
            rejected.exception.close()
            if statuses[-1] == HTTPStatus.TOO_MANY_REQUESTS:
                break

        self.assertIn(HTTPStatus.TOO_MANY_REQUESTS, statuses)

    def test_authenticated_non_cloudflare_proxy_uses_its_authenticated_client_ip(self) -> None:
        runtime = PublicInviteRuntime(
            environ={
                "AGENTSASSEMBLE_TRUSTED_PROXY_TOKEN": "generic-proxy-secret",
            }
        )
        runtime.set_public_url("https://generic-proxy.example.com")
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            _make_handler(
                self.root / "generic-proxy",
                public_invite_runtime_override=runtime,
            ),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        statuses: list[int] = []
        try:
            endpoint = (
                f"http://127.0.0.1:{server.server_port}"
                "/api/identity/recovery-code/redeem"
            )
            for index in range(16):
                with self.assertRaises(HTTPError) as rejected:
                    urlopen(
                        _json_request(
                            endpoint,
                            {
                                "recovery_code": f"generic-invalid-code-{index}",
                                "room_id": "friend-room",
                                "device_token": "generic-proxy-device",
                                "client_id": f"generic-proxy-client-{index}",
                            },
                            {
                                "Host": "generic-proxy.example.com",
                                "Origin": "https://generic-proxy.example.com",
                                "X-Forwarded-Proto": "https",
                                "X-AgentsAssemble-Proxy-Token": "generic-proxy-secret",
                                "X-AgentsAssemble-Client-IP": "198.51.100.20",
                                "CF-Connecting-IP": f"198.51.100.{index + 20}",
                            },
                        ),
                        timeout=4,
                    )
                statuses.append(rejected.exception.code)
                rejected.exception.close()

            with self.assertRaises(HTTPError) as other_client:
                urlopen(
                    _json_request(
                        endpoint,
                        {
                            "recovery_code": "generic-other-client-code",
                            "room_id": "friend-room",
                            "device_token": "generic-proxy-device",
                            "client_id": "generic-other-client",
                        },
                        {
                            "Host": "generic-proxy.example.com",
                            "Origin": "https://generic-proxy.example.com",
                            "X-Forwarded-Proto": "https",
                            "X-AgentsAssemble-Proxy-Token": "generic-proxy-secret",
                            "X-AgentsAssemble-Client-IP": "198.51.100.21",
                        },
                    ),
                    timeout=4,
                )
            other_client.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(statuses, [HTTPStatus.FORBIDDEN] * 16)
        self.assertEqual(other_client.exception.code, HTTPStatus.FORBIDDEN)

    def test_recovery_code_aliases_share_one_attempt_budget(self) -> None:
        endpoint = f"{self.base}/api/identity/recovery-code/redeem"
        normalized = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        aliases = [
            normalized.lower(),
            f"{normalized[:4]}-{normalized[4:]}",
            f"{normalized[:8]} {normalized[8:]}",
            f"{normalized[:12]}-{normalized[12:]}",
            f"{normalized[:16]} {normalized[16:]}",
            f"{normalized[:20]}-{normalized[20:]}",
            f"{normalized[:24]} {normalized[24:]}",
            f"{normalized[:28]}-{normalized[28:]}",
            f" {normalized.lower()} ",
        ]
        statuses: list[int] = []
        for index, alias in enumerate(aliases):
            with self.assertRaises(HTTPError) as rejected:
                urlopen(
                    _json_request(
                        endpoint,
                        {
                            "recovery_code": alias,
                            "room_id": "friend-room",
                            "device_token": "alias-device",
                            "client_id": f"alias-client-{index}",
                        },
                        {
                            **self.public_headers,
                            "CF-Connecting-IP": f"203.0.113.{index + 40}",
                        },
                    ),
                    timeout=4,
                )
            statuses.append(rejected.exception.code)
            rejected.exception.close()

        self.assertEqual(statuses[:8], [HTTPStatus.FORBIDDEN] * 8)
        self.assertEqual(statuses[8], HTTPStatus.TOO_MANY_REQUESTS)
