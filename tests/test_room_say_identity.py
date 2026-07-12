"""Regression test: /api/room/say uses session identity, not client-supplied fields."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from agentsassemble.gui import _make_handler
from agentsassemble.room_invite import reset_state, verify_session_token
from agentsassemble.room_speech import SERVER_AUTO_CHAIN_DEPTH_LIMIT


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

    def _get_json(self, path: str, query: dict[str, str] | None = None) -> dict:
        suffix = f"?{urlencode(query)}" if query else ""
        with urlopen(f"{self.url}{path}{suffix}", timeout=4) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_say_uses_session_identity_not_client_fields(self):
        # 1. Create invite
        invite = self._post("/api/room-invite/create", {
            "meeting_id": "test-m",
            "agent_id": "review-guest",
            "display_name": "Review Guest",
            "local_dev_preview": True,
            "max_uses": 1,
        })
        self.assertIn("invite_token", invite)
        packet = invite["remote_client_packet"]
        self.assertEqual(packet["packet_kind"], "agent_attendee_entry_packet")
        self.assertEqual(packet["agent"]["agent_id"], "review-guest")
        self.assertNotIn("env", packet)
        self.assertNotIn("http", packet)

        # 2. Join
        join = self._post("/api/room-invite/join", {
            "invite_token": invite["invite_token"],
            "meeting_id": "test-m",
        })
        self.assertEqual(join["status"], "admitted")
        session_token = join["session_token"]
        self.assertEqual(join["display_name"], "Review Guest")
        self.assertEqual(join["agent_id"], "review-guest")
        self.assertEqual(join["participant_type"], "human")

        members = self._get_json("/api/room-members", {"meeting_id": "test-m"})["members"]
        review_guest = next(member for member in members if member["participant_id"] == "review-guest")
        self.assertEqual(review_guest["role"], "human")
        self.assertEqual(review_guest["participant_type"], "human")

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

    def test_joined_friend_can_create_companion_ai_packet_for_same_room(self):
        invite = self._post("/api/room-invite/create", {
            "meeting_id": "friend-room",
            "agent_id": "friend-human",
            "display_name": "Friend Human",
            "local_dev_preview": True,
            "max_uses": 1,
        })
        friend_join = self._post("/api/room-invite/join", {
            "invite_token": invite["invite_token"],
        })
        friend_session_token = friend_join["session_token"]

        companion = self._post("/api/room-invite/companion", {
            "agent_id": "friend-ai",
            "display_name": "Friend AI",
        }, token=friend_session_token)

        self.assertEqual(companion["meeting_id"], "friend-room")
        self.assertEqual(companion["agent_id"], "friend-ai")
        packet = companion["remote_client_packet"]
        self.assertEqual(packet["packet_kind"], "agent_attendee_entry_packet")
        self.assertEqual(packet["agent"]["meeting_id"], "friend-room")
        self.assertEqual(packet["agent"]["agent_id"], "friend-ai")

        ai_join = self._post("/api/room-invite/join", {
            "invite_token": companion["invite_token"],
        })
        self.assertEqual(ai_join["participant_type"], "remote")
        ai_session_token = ai_join["session_token"]

        members = self._get_json("/api/room-members", {"meeting_id": "friend-room"})["members"]
        human = next(member for member in members if member["participant_id"] == "friend-human")
        ai = next(member for member in members if member["participant_id"] == "friend-ai")
        self.assertEqual(human["role"], "human")
        self.assertEqual(human["participant_type"], "human")
        self.assertEqual(ai["role"], "agent")
        self.assertEqual(ai["participant_type"], "remote")

        event_resp = self._post("/api/room/say", {
            "message": "friend ai is here",
        }, token=ai_session_token)

        event = event_resp["event"]
        self.assertEqual(event["flow_meeting_id"], "friend-room")
        self.assertEqual(event["actor_id"], "friend-ai")
        self.assertEqual(event["name"], "Friend AI")
        self.assertEqual(event["message"], "friend ai is here")

    def test_say_rejects_over_depth_auto_reply_as_conflict(self):
        invite = self._post("/api/room-invite/create", {
            "meeting_id": "test-m",
            "agent_id": "review-guest",
            "display_name": "Review Guest",
            "local_dev_preview": True,
            "max_uses": 1,
        })
        join = self._post("/api/room-invite/join", {
            "invite_token": invite["invite_token"],
            "meeting_id": "test-m",
        })
        request = Request(
            f"{self.url}/api/room/say",
            data=json.dumps(
                {
                    "message": "too deep",
                    "source_event_id": "src1",
                    "auto_chain_depth": SERVER_AUTO_CHAIN_DEPTH_LIMIT + 1,
                }
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {join['session_token']}",
            },
            method="POST",
        )

        with self.assertRaises(HTTPError) as rejected:
            urlopen(request, timeout=4)

        self.assertEqual(rejected.exception.code, 409)
        rejected.exception.close()


if __name__ == "__main__":
    unittest.main()
