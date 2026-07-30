from tests.gui_server_test_support import (
    RoomStore,
    ThreadingHTTPServer,
    _make_handler,
    json,
    tempfile,
    threading,
    unittest,
    urlopen,
)
from urllib.error import HTTPError
from urllib.request import Request


class RoomDirectoryHttpTests(unittest.TestCase):

    def test_inactive_room_appearance_is_available_from_the_room_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RoomStore(temp_dir)
            store.create_room("custom-room", label="Custom Room")
            store.update_room_settings(
                "custom-room",
                {
                    "topic": "Canonical topic",
                    "appearance": {
                        "banner_preset": "custom",
                        "banner_image_url": "/api/attachments/banner01?view=1",
                        "icon_image_url": "/api/attachments/icon0001?view=1",
                        "icon_label": "C",
                        "invite_scope": "room",
                    },
                },
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(store.output_root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/rooms",
                    timeout=4,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

        room = next(item for item in payload["rooms"] if item["room_id"] == "custom-room")
        self.assertEqual(room["label"], "Custom Room")
        self.assertEqual(room["room_settings"]["topic"], "Canonical topic")
        self.assertEqual(
            room["room_settings"]["appearance"]["banner_image_url"],
            "/api/attachments/banner01?view=1",
        )
        self.assertEqual(
            room["room_settings"]["appearance"]["icon_image_url"],
            "/api/attachments/icon0001?view=1",
        )

    def test_http_cannot_bypass_the_canonical_room_settings_event_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RoomStore(temp_dir)
            store.create_room("settings-room", label="Original")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(store.output_root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            request = Request(
                f"http://127.0.0.1:{server.server_port}/api/room-settings",
                data=json.dumps(
                    {"room_id": "settings-room", "label": "Bypassed"}
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with self.assertRaises(HTTPError) as rejected:
                    urlopen(request, timeout=4)
            finally:
                server.shutdown()
                server.server_close()
            stored_label = store.room_settings("settings-room")["label"]

        self.assertEqual(rejected.exception.code, 409)
        self.assertEqual(stored_label, "Original")


if __name__ == "__main__":
    unittest.main()
