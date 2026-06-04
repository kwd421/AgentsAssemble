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

from agentsassemble.gui import _make_handler
from agentsassemble.public_tunnel import PublicTunnelManager
from agentsassemble.room_invite import reset_state


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

    def test_gui_can_bootstrap_host_token_public_url_and_join_link(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENTSASSEMBLE_HOST_TOKEN", None)
            os.environ.pop("AGENTSASSEMBLE_PUBLIC_URL", None)
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir) / "room"
                dist = Path(temp_dir) / "dist"
                assets = dist / "assets"
                assets.mkdir(parents=True)
                (dist / "index.html").write_text(
                    '<div id="root">react app</div><script type="module" src="/assets/app.js"></script>',
                    encoding="utf-8",
                )
                (assets / "app.js").write_text("console.log('join');", encoding="utf-8")
                tunnel = PublicTunnelManager(which=lambda _name: None)
                server = ThreadingHTTPServer(
                    ("127.0.0.1", 0),
                    _make_handler(root, frontend_dist_root=dist, public_tunnel_manager=tunnel),
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
                    self.assertEqual(blocked_operator_route.exception.code, 403)

                    with urlopen(
                        _json_request(
                            f"{base}/api/room-invite/join",
                            {"invite_token": invite["invite_token"]},
                            public_headers,
                        ),
                        timeout=4,
                    ) as response:
                        session_payload = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(session_payload["status"], "admitted")

                    with urlopen(
                        Request(
                            f"{base}/api/room/lobby",
                            headers={
                                **public_headers,
                                "Authorization": f"Bearer {session_payload['session_token']}",
                            },
                        ),
                            timeout=4,
                        ) as response:
                            lobby_payload = json.loads(response.read().decode("utf-8"))
                    self.assertIn("events", lobby_payload)

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
                    self.assertEqual(companion["agent_id"], "friend-ai")
                    self.assertEqual(
                        companion["remote_client_packet"]["env"]["AGENTSASSEMBLE_ROOM_URL"],
                        "https://shared-room.example.com",
                    )
                finally:
                    server.shutdown()
                    server.server_close()

    def test_tunnel_start_reports_missing_cloudflared_without_exposing_token(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENTSASSEMBLE_HOST_TOKEN", None)
            os.environ.pop("AGENTSASSEMBLE_PUBLIC_URL", None)
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                tunnel = PublicTunnelManager(which=lambda _name: None)
                server = ThreadingHTTPServer(
                    ("127.0.0.1", 0),
                    _make_handler(root, public_tunnel_manager=tunnel),
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


if __name__ == "__main__":
    unittest.main()
