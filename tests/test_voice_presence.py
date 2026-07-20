"""Voice-channel presence (CH-4): the entity + join/leave/roster, before audio.

Unit tests drive the TTL registry directly (injected clock); HTTP tests drive
the governed join/leave/presence routes against a real server.
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
from agentsassemble.admission.invite import (
    create_room_invite,
    join_room_with_invite,
    reset_state,
    set_runtime_host_token,
)
from agentsassemble.room_store import RoomStore
from agentsassemble.room.voice_presence import (
    join_voice,
    leave_all_voice,
    leave_voice,
    reset_voice_presence,
    voice_participants,
    voice_presence_for_room,
)


class VoicePresenceRegistryTests(unittest.TestCase):
    def setUp(self):
        reset_voice_presence()

    def tearDown(self):
        reset_voice_presence()

    def test_join_heartbeat_and_ttl_expiry(self):
        join_voice("r1", "c1", "p1", display_name="Alice", now=0.0)
        join_voice("r1", "c1", "p2", display_name="Bob", now=0.0)
        members = voice_participants("r1", "c1", now=1.0)
        self.assertEqual([m["name"] for m in members], ["Alice", "Bob"])  # sorted by name

        # p1 heartbeats at t=40 (refreshes TTL); p2 does not.
        join_voice("r1", "c1", "p1", display_name="Alice", now=40.0)
        # at t=50, p2's original 45s TTL has expired; p1's refreshed one survives.
        alive = voice_participants("r1", "c1", now=50.0)
        self.assertEqual([m["participant_id"] for m in alive], ["p1"])

    def test_leave_and_leave_all(self):
        join_voice("r1", "c1", "p1", display_name="A", now=0.0)
        join_voice("r1", "c2", "p1", display_name="A", now=0.0)
        leave_voice("r1", "c1", "p1")
        self.assertEqual(voice_participants("r1", "c1", now=1.0), [])
        self.assertEqual(len(voice_participants("r1", "c2", now=1.0)), 1)
        leave_all_voice("r1", "p1")  # e.g. on kick: drop from every voice channel
        self.assertEqual(voice_participants("r1", "c2", now=1.0), [])

    def test_presence_for_room_groups_by_channel_and_omits_empty(self):
        join_voice("r1", "c1", "p1", display_name="A", now=0.0)
        join_voice("r1", "c2", "p2", display_name="B", now=0.0)
        join_voice("r2", "c9", "p9", display_name="Z", now=0.0)  # other room, ignored
        presence = voice_presence_for_room("r1", now=1.0)
        self.assertEqual(set(presence), {"c1", "c2"})
        self.assertEqual(presence["c1"][0]["participant_id"], "p1")


class VoicePresenceHttpTests(unittest.TestCase):
    def setUp(self):
        reset_state()
        reset_voice_presence()
        self._servers: list[ThreadingHTTPServer] = []

    def tearDown(self):
        for server in self._servers:
            server.shutdown()
            server.server_close()
        reset_state()
        reset_voice_presence()

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

    def _token(self, base: str, meeting_id: str, agent_id: str) -> str:
        invite = create_room_invite(
            room_url=base, meeting_id=meeting_id, agent_id=agent_id,
            display_name=agent_id, participant_type="human", max_uses=5,
        )
        return str(join_room_with_invite(str(invite["invite_token"]))["session_token"])

    def _post(self, base: str, path: str, token: str, payload: dict) -> dict:
        request = Request(
            f"{base}{path}",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=4) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_join_leave_presence_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            set_runtime_host_token("host-secret")
            root = Path(temp_dir) / "room"
            RoomStore(root).create_room("room-1", label="Room 1")
            base = self._start(root)
            voice_id = self._create_channel(base, "room-1", "음성", "voice")
            alice = self._token(base, "room-1", "alice")
            bob = self._token(base, "room-1", "bob")

            joined = self._post(base, "/api/room/voice/join", alice, {"channel_id": voice_id})
            self.assertEqual(joined["channel_id"], voice_id)
            self.assertEqual([p["name"] for p in joined["participants"]][:1], ["alice"])

            self._post(base, "/api/room/voice/join", bob, {"channel_id": voice_id, "muted": True})
            request = Request(
                f"{base}/api/room/voice?channel_id={voice_id}",
                headers={"Authorization": f"Bearer {alice}"},
            )
            with urlopen(request, timeout=4) as response:
                presence = json.loads(response.read().decode("utf-8"))
            names = {p["name"]: p["muted"] for p in presence["participants"]}
            self.assertTrue({"alice", "bob"} <= set(names))
            self.assertTrue(names["bob"])  # joined muted

            left = self._post(base, "/api/room/voice/leave", bob, {"channel_id": voice_id})
            self.assertNotIn("bob", {p["name"] for p in left["participants"]})

    def test_join_rejects_text_channel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            set_runtime_host_token("host-secret")
            root = Path(temp_dir) / "room"
            RoomStore(root).create_room("room-1", label="Room 1")
            base = self._start(root)
            text_id = self._create_channel(base, "room-1", "구현방", "text")
            token = self._token(base, "room-1", "alice")
            with self.assertRaises(HTTPError) as ctx:
                self._post(base, "/api/room/voice/join", token, {"channel_id": text_id})
            self.assertEqual(ctx.exception.code, 400)
            ctx.exception.close()


if __name__ == "__main__":
    unittest.main()
