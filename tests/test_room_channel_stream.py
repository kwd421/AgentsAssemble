"""Per-channel text streams through current session-gated room routes."""
import json
import secrets
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agentsassemble.gui import _make_handler
from agentsassemble.application.public_invite_runtime import PublicInviteRuntime
from agentsassemble.room_store import RoomStore
from agentsassemble.web.room_client import join_room_session


class RoomChannelStreamTests(unittest.TestCase):
    def setUp(self):
        self.public_invite = PublicInviteRuntime(environ={})
        self._servers: list[ThreadingHTTPServer] = []

    def tearDown(self):
        for server in self._servers:
            server.shutdown()
            server.server_close()

    def _start(self, root: Path) -> str:
        RoomStore(root).create_room("room-1", label="Room 1")
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            _make_handler(root, public_invite_runtime_override=self.public_invite),
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self._servers.append(server)
        return f"http://127.0.0.1:{server.server_port}"

    def _create_channel(self, root: Path, meeting_id: str, name: str, channel_type: str) -> str:
        repository = RoomStore(root)
        channel_id = f"c{secrets.token_hex(6)}"
        current = repository.room_settings(meeting_id)
        repository.update_room_settings(
            meeting_id,
            {
                "channels": [
                    *current["channels"],
                    {
                        "id": channel_id,
                        "name": name,
                        "type": channel_type,
                        "position": len(current["channels"]),
                        "created_at": "2026-07-30T00:00:00Z",
                    },
                ]
            },
        )
        return channel_id

    def _token(self, base: str, meeting_id: str) -> str:
        request = Request(
            f"{base}/api/room-invite/create",
            data=json.dumps(
                {
                    "meeting_id": meeting_id,
                    "agent_id": "human-1",
                    "display_name": "Human",
                    "participant_type": "human",
                    "max_uses": 1,
                    "local_dev_preview": True,
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=4) as response:
            invite = json.loads(response.read().decode("utf-8"))
        return str(
            join_room_session(
                base,
                str(invite["invite_token"]),
                display_name="Human",
                participant_type="human",
            )["session_token"]
        )

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
            self.public_invite.set_host_token("host-secret")
            root = Path(temp_dir) / "room"
            base = self._start(root)
            channel_id = self._create_channel(root, "room-1", "구현방", "text")
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

    def test_channel_stream_is_isolated_from_canonical_room_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.public_invite.set_host_token("host-secret")
            root = Path(temp_dir) / "room"
            base = self._start(root)
            channel_id = self._create_channel(root, "room-1", "구현방", "text")
            token = self._token(base, "room-1")
            self._say(base, token, channel_id, "채널 전용")

            # A custom-channel append must not create a canonical main-room event.
            canonical = RoomStore(Path(temp_dir) / "room").read_events("room-1")
            self.assertEqual(
                [event for event in canonical if event.get("content") == "채널 전용"],
                [],
            )

    def test_say_rejects_voice_and_unknown_channel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.public_invite.set_host_token("host-secret")
            root = Path(temp_dir) / "room"
            base = self._start(root)
            voice_id = self._create_channel(root, "room-1", "음성", "voice")
            token = self._token(base, "room-1")
            with self.assertRaises(HTTPError) as ctx:
                self._say(base, token, voice_id, "안돼")
            self.assertEqual(ctx.exception.code, 400)  # not a text channel
            ctx.exception.close()
            with self.assertRaises(HTTPError) as ctx:
                self._say(base, token, "cffffffffffff", "유령")
            self.assertEqual(ctx.exception.code, 404)  # unknown channel
            ctx.exception.close()

    def test_loopback_local_console_reads_and_says_without_session(self):
        # The local operator console (loopback, no session) can use canonical
        # channel routes. Public-tunnel callers still require a room session.
        with tempfile.TemporaryDirectory() as temp_dir:
            self.public_invite.set_host_token("host-secret")
            root = Path(temp_dir) / "room"
            base = self._start(root)
            channel_id = self._create_channel(root, "room-1", "구현방", "text")

            say = Request(
                f"{base}/api/room/channel-say",
                data=json.dumps({"meeting_id": "room-1", "channel_id": channel_id, "message": "로컬", "name": "운영자"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(say, timeout=4) as response:
                posted = json.loads(response.read().decode("utf-8"))
            self.assertEqual(posted["event"]["message"], "로컬")
            self.assertEqual(posted["event"]["name"], "운영자")  # loopback trusts supplied name
            self.assertEqual(posted["event"]["actor_type"], "human")

            read = Request(f"{base}/api/room/channel-lobby?channel_id={channel_id}&meeting_id=room-1")
            with urlopen(read, timeout=4) as response:
                events = json.loads(response.read().decode("utf-8"))["events"]
            self.assertEqual([e["message"] for e in events], ["로컬"])


if __name__ == "__main__":
    unittest.main()
