from __future__ import annotations

import json
import socket
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from agentsassemble.gui import _make_handler
from agentsassemble.room_invite import reset_state, set_runtime_host_token, set_runtime_public_url
from agentsassemble.room_realtime import RoomRealtimeController
from agentsassemble.ws_room_client import WsRoomClient


PUBLIC_HOST = "shared-room.example.com"
PUBLIC_ORIGIN = f"https://{PUBLIC_HOST}"


def _post_json(
    url: str,
    payload: dict[str, object],
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    request_headers = {"Content-Type": "application/json", **dict(headers or {})}
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urlopen(request, timeout=4.0) as response:
        return json.loads(response.read().decode("utf-8"))


class CanonicalRoomSocialFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_state()

    def tearDown(self) -> None:
        reset_state()

    def test_friend_add_invite_join_and_canonical_websocket_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = RoomRealtimeController(root, providers=[])
            set_runtime_host_token("host-secret")
            set_runtime_public_url(PUBLIC_ORIGIN)
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(root, room_realtime_controller_override=controller),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            client: WsRoomClient | None = None
            try:
                saved = _post_json(
                    f"{base}/api/room-friends",
                    {
                        "friend_id": "friend:moon",
                        "display_name": "Moon",
                        "handle": "moon",
                        "participant_type": "human",
                    },
                )
                friend = saved["friend"]
                self.assertEqual(friend["friend_id"], "friend:moon")

                member = _post_json(
                    f"{base}/api/room-members",
                    {
                        "meeting_id": "general",
                        "participant_id": "friend:moon",
                        "display_name": "Moon",
                        "role": "human",
                        "participant_type": "human",
                        "status": "invited",
                        "source": "friend_invite",
                    },
                )["member"]
                self.assertEqual(member["source"], "friend_invite")
                self.assertEqual(member["status"], "invited")

                invite = _post_json(
                    f"{base}/api/room-invite/create",
                    {
                        "meeting_id": "general",
                        "agent_id": "friend:moon",
                        "display_name": "Moon",
                        "max_uses": 1,
                    },
                    headers={"X-Host-Token": "host-secret"},
                )
                self.assertTrue(str(invite["join_url"]).startswith(f"{PUBLIC_ORIGIN}/join?token="))

                public_headers = {"Host": PUBLIC_HOST, "Origin": PUBLIC_ORIGIN}
                joined = _post_json(
                    f"{base}/api/room-invite/join",
                    {"invite_token": invite["invite_token"]},
                    headers=public_headers,
                )
                self.assertEqual(joined["status"], "admitted")
                self.assertEqual(joined["agent_id"], "friend:moon")

                ticket = _post_json(
                    f"{base}/api/ws-ticket",
                    {},
                    headers={
                        **public_headers,
                        "Authorization": f"Bearer {joined['session_token']}",
                    },
                )["ticket"]
                sock = socket.create_connection(("127.0.0.1", server.server_port), timeout=4.0)
                client = WsRoomClient(sock, host=PUBLIC_HOST)
                client.open(f"/ws?ticket={ticket}")
                client.subscribe(["room_events"])
                client.sock.settimeout(0.2)
                request_id = client.command(
                    "message.send",
                    {"content": "초대받은 친구의 canonical WebSocket 메시지"},
                    request_id="friend-message",
                )
                ack = self._receive_until(
                    client,
                    lambda message: message.get("op") == "ack" and message.get("request_id") == request_id,
                )
                event = ack["result"]["event"]

                self.assertEqual(event["actor"]["participant_id"], "friend:moon")
                self.assertEqual(event["room_id"], "general")
                self.assertEqual(event["content"], "초대받은 친구의 canonical WebSocket 메시지")
                self.assertEqual(controller.store.participant("general", "friend:moon")["status"], "joined")
                self.assertEqual(controller.store.read_events("general")[-1]["id"], event["id"])
            finally:
                if client is not None:
                    client.close()
                controller.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3.0)

    @staticmethod
    def _receive_until(client: WsRoomClient, predicate, *, rounds: int = 50) -> dict[str, object]:
        for _ in range(rounds):
            for message in client.receive():
                if predicate(message):
                    return message
        raise TimeoutError("Expected canonical room WebSocket message was not received.")


if __name__ == "__main__":
    unittest.main()
