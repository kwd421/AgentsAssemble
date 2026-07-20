"""HTTP CRUD for custom room channels (CH-2), against a real server.

Channel mutations are moderator-gated (host token or operator session), same as
mute/kick; reads are open to a loopback console. These tests drive the actual
/api/room-channels route through the governed request path.
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
from agentsassemble.admission.invite import reset_state, set_runtime_host_token
from agentsassemble.room_store import RoomStore


class RoomChannelsHttpTests(unittest.TestCase):
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

    def _post(self, base: str, payload: dict, *, host_token: str | None = "host-secret") -> dict:
        headers = {"Content-Type": "application/json"}
        if host_token is not None:
            headers["X-Host-Token"] = host_token
        request = Request(
            f"{base}/api/room-channels",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=4) as response:
            return json.loads(response.read().decode("utf-8"))

    def _list(self, base: str, meeting_id: str) -> dict:
        request = Request(f"{base}/api/room-channels?meeting_id={meeting_id}")
        with urlopen(request, timeout=4) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_create_rename_reorder_delete_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            set_runtime_host_token("host-secret")
            root = Path(temp_dir) / "room"
            RoomStore(root).create_room("r1", label="Room 1")
            base = self._start(root)

            created = self._post(base, {"meeting_id": "r1", "action": "create", "name": "구현방", "type": "text"})
            self.assertEqual(created["channel"]["name"], "구현방")
            self.assertEqual(created["channel"]["type"], "text")
            text_id = created["channel"]["id"]

            voice = self._post(base, {"meeting_id": "r1", "action": "create", "name": "음성 라운지", "type": "voice"})
            voice_id = voice["channel"]["id"]
            self.assertEqual(voice["channel"]["type"], "voice")
            self.assertEqual([c["id"] for c in voice["channels"]], [text_id, voice_id])

            renamed = self._post(base, {"meeting_id": "r1", "action": "rename", "channel_id": text_id, "name": "구현-1"})
            self.assertEqual(renamed["channels"][0]["name"], "구현-1")

            reordered = self._post(base, {"meeting_id": "r1", "action": "reorder", "ordered_ids": [voice_id, text_id]})
            self.assertEqual([c["id"] for c in reordered["channels"]], [voice_id, text_id])

            # GET reflects the persisted, reordered list (loopback reads freely).
            listed = self._list(base, "r1")
            self.assertEqual([c["id"] for c in listed["channels"]], [voice_id, text_id])

            deleted = self._post(base, {"meeting_id": "r1", "action": "delete", "channel_id": voice_id})
            self.assertEqual([c["id"] for c in deleted["channels"]], [text_id])
            self.assertEqual(deleted["channels"][0]["position"], 0)

    def test_mutation_requires_moderator(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            set_runtime_host_token("host-secret")
            root = Path(temp_dir) / "room"
            RoomStore(root).create_room("r1", label="Room 1")
            base = self._start(root)
            with self.assertRaises(HTTPError) as ctx:
                self._post(base, {"meeting_id": "r1", "action": "create", "name": "x"}, host_token="wrong")
            self.assertEqual(ctx.exception.code, 403)
            ctx.exception.close()

    def test_missing_room_read_returns_not_found(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "room"
            base = self._start(root)

            with self.assertRaises(HTTPError) as ctx:
                self._list(base, "missing-room")

            self.assertEqual(ctx.exception.code, 404)
            ctx.exception.close()

    def test_error_categories_map_to_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            set_runtime_host_token("host-secret")
            root = Path(temp_dir) / "room"
            RoomStore(root).create_room("r1", label="Room 1")
            base = self._start(root)
            with self.assertRaises(HTTPError) as ctx:
                self._post(base, {"meeting_id": "r1", "action": "create", "name": "   "})
            self.assertEqual(ctx.exception.code, 400)  # empty name
            ctx.exception.close()
            with self.assertRaises(HTTPError) as ctx:
                self._post(base, {"meeting_id": "r1", "action": "delete", "channel_id": "ghost"})
            self.assertEqual(ctx.exception.code, 404)  # unknown channel
            ctx.exception.close()
            with self.assertRaises(HTTPError) as ctx:
                self._post(base, {"meeting_id": "r1", "action": "wat"})
            self.assertEqual(ctx.exception.code, 400)  # unknown action
            ctx.exception.close()


if __name__ == "__main__":
    unittest.main()
