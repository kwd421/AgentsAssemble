"""Regression test: /api/room/say uses session identity, not client-supplied fields."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from agentsassemble.gui import _make_handler
from agentsassemble.room_invite import reset_state, verify_session_token


class TestRoomSayIdentity(unittest.TestCase):
    """create -> join -> say -> leave through HTTP, proving identity enforcement."""

    def setUp(self):
        reset_state()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()
        reset_state()

    def _post(self, path: str, body: dict, token: str | None = None) -> dict:
        data = json.dumps(body).encode("utf-8")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = Request(f"{self.url}{path}", data=data, headers=headers, method="POST")
        with urlopen(req, timeout=4) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_say_uses_session_identity_not_client_fields(self):
        # 1. Create invite
        invite = self._post("/api/room-invite/create", {
            "meeting_id": "test-m",
            "agent_id": "review-guest",
            "display_name": "Review Guest",
        })
        self.assertIn("invite_token", invite)

        # 2. Join
        join = self._post("/api/room-invite/join", {
            "invite_token": invite["invite_token"],
            "meeting_id": "test-m",
        })
        self.assertEqual(join["status"], "admitted")
        session_token = join["session_token"]
        self.assertEqual(join["display_name"], "Review Guest")
        self.assertEqual(join["agent_id"], "review-guest")

        # 3. Say — client tries to spoof identity
        event_resp = self._post("/api/room/say", {
            "message": "hello room",
            "name": "Spoofed Name",
            "actor_id": "spoofed-id",
            "side": "mine",
            "kind": "deploy",
        }, token=session_token)
        event = event_resp["event"]

        # Identity must come from session, not client payload
        self.assertEqual(event["name"], "Review Guest")
        self.assertEqual(event["actor_id"], "review-guest")
        self.assertEqual(event["side"], "other")
        self.assertEqual(event["kind"], "message")
        self.assertEqual(event["message"], "hello room")

        # 4. Leave
        leave_req = Request(
            f"{self.url}/api/room-invite/leave",
            data=json.dumps({}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {session_token}",
            },
            method="POST",
        )
        with urlopen(leave_req, timeout=4) as resp:
            leave = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(leave["status"], "left")
        self.assertEqual(leave["agent_id"], "review-guest")

        # Session must be revoked after leave
        self.assertIsNone(verify_session_token(session_token))


if __name__ == "__main__":
    unittest.main()
