"""Per-channel text streams (CH-3): each custom text channel has its own
message file, reachable through session-gated say/read routes, isolated from the
main lobby. Driven end-to-end against a real server.
"""
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agentsassemble.gui import _make_handler
from agentsassemble.room_invite import (
    create_room_invite,
    join_room_with_invite,
    reset_state,
    set_runtime_host_token,
)


class RoomChannelStreamTests(unittest.TestCase):
    def setUp(self):
        reset_state()
        self._servers: list[ThreadingHTTPServer] = []

    def tearDown(self):
        for server in self._servers:
            server.shutdown()
            server.server_close()
        reset_state()

    def _start(self, root: Path) -> str:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self._servers.append(server)
        return f"http://127.0.0.1:{server.server_port}"

    def _create_channel(self, base: str, meeting_id: str, name: str, channel_type: str) -> str:
        request = Request(
            f"{base}/api/room-channels",
            data=json.dumps({"meeting_id": meeting_id, "action": "create", "name": name, "type": channel_type}).encode(),
            headers={"Content-Type": "application/json", "X-Host-Token": "host-secret"},
            method="POST",
        )
        with urlopen(request, timeout=4) as response:
            return json.loads(response.read().decode("utf-8"))["channel"]["id"]

    def _token(self, base: str, meeting_id: str) -> str:
        invite = create_room_invite(
            room_url=base, meeting_id=meeting_id, agent_id="human-1",
            display_name="Human", participant_type="human", max_uses=5,
        )
        return str(join_room_with_invite(str(invite["invite_token"]))["session_token"])

    def _say(self, base: str, token: str, channel_id: str, message: str) -> dict:
        request = Request(
            f"{base}/api/room/channel-say",
            data=json.dumps({"channel_id": channel_id, "message": message}).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=4) as response:
            return json.loads(response.read().decode("utf-8"))

    def _read(self, base: str, token: str, channel_id: str, *, after: str = "") -> list[dict]:
        url = f"{base}/api/room/channel-lobby?channel_id={channel_id}"
        if after:
            url += f"&after={after}"
        request = Request(url, headers={"Authorization": f"Bearer {token}"})
        with urlopen(request, timeout=4) as response:
            return json.loads(response.read().decode("utf-8"))["events"]

    def test_text_channel_say_read_and_after_cursor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            set_runtime_host_token("host-secret")
            base = self._start(Path(temp_dir) / "room")
            channel_id = self._create_channel(base, "room-1", "구현방", "text")
            token = self._token(base, "room-1")

            first = self._say(base, token, channel_id, "첫 메시지")
            self.assertEqual(first["channel_id"], channel_id)
            self.assertEqual(first["event"]["message"], "첫 메시지")
            # identity stamped from the session (id may be disambiguated on join)
            self.assertTrue(first["event"]["actor_id"].startswith("human-1"))
            self.assertEqual(first["event"]["actor_type"], "human")

            self._say(base, token, channel_id, "둘째 메시지")
            events = self._read(base, token, channel_id)
            self.assertEqual([e["message"] for e in events], ["첫 메시지", "둘째 메시지"])

            # after-cursor returns only what followed the given id (polling).
            after = self._read(base, token, channel_id, after=events[0]["id"])
            self.assertEqual([e["message"] for e in after], ["둘째 메시지"])

    def test_channel_stream_is_isolated_from_main_lobby(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            set_runtime_host_token("host-secret")
            base = self._start(Path(temp_dir) / "room")
            channel_id = self._create_channel(base, "room-1", "구현방", "text")
            token = self._token(base, "room-1")
            self._say(base, token, channel_id, "채널 전용")

            # The main lobby must not contain the channel message.
            request = Request(f"{base}/api/room/lobby", headers={"Authorization": f"Bearer {token}"})
            with urlopen(request, timeout=4) as response:
                lobby = json.loads(response.read().decode("utf-8"))["events"]
            self.assertEqual([e for e in lobby if e.get("message") == "채널 전용"], [])

    def test_say_rejects_voice_and_unknown_channel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            set_runtime_host_token("host-secret")
            base = self._start(Path(temp_dir) / "room")
            voice_id = self._create_channel(base, "room-1", "음성", "voice")
            token = self._token(base, "room-1")
            with self.assertRaises(HTTPError) as ctx:
                self._say(base, token, voice_id, "안돼")
            self.assertEqual(ctx.exception.code, 400)  # not a text channel
            with self.assertRaises(HTTPError) as ctx:
                self._say(base, token, "cffffffffffff", "유령")
            self.assertEqual(ctx.exception.code, 404)  # unknown channel

    def test_read_requires_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            set_runtime_host_token("host-secret")
            base = self._start(Path(temp_dir) / "room")
            channel_id = self._create_channel(base, "room-1", "구현방", "text")
            request = Request(f"{base}/api/room/channel-lobby?channel_id={channel_id}")
            with self.assertRaises(HTTPError) as ctx:
                urlopen(request, timeout=4)
            self.assertEqual(ctx.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
