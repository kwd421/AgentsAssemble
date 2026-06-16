from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.room_settings import room_settings_payload, update_room_settings


class RoomSettingsTests(unittest.TestCase):
    def test_room_settings_persist_channel_notification_and_read_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            saved = update_room_settings(
                root,
                {
                    "room_id": "resident-m1",
                    "label": "AgentsAssemble",
                    "topic": "상주 회의실",
                    "channel_settings": {
                        "lobby": {
                            "notifications": "mute",
                            "last_read_at": "2026-06-03T10:20:30Z",
                        },
                        "live": {
                            "notifications": "all",
                            "last_read_at": " 2026-06-03\t10:21 ",
                        },
                        "unknown": {
                            "notifications": "mute",
                        },
                        "records": {
                            "notifications": "not-real",
                        },
                    },
                },
            )
            loaded = room_settings_payload(root, room_id="resident-m1")

        channels = saved["settings"]["channel_settings"]
        self.assertEqual(channels["lobby"]["notifications"], "mute")
        self.assertEqual(channels["lobby"]["last_read_at"], "2026-06-03T10:20:30Z")
        self.assertEqual(channels["live"]["notifications"], "all")
        self.assertEqual(channels["live"]["last_read_at"], "2026-06-03 10:21")
        self.assertEqual(channels["records"]["notifications"], "default")
        self.assertNotIn("unknown", channels)
        self.assertEqual(loaded["settings"]["channel_settings"], channels)

    def test_room_settings_partial_update_preserves_channel_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            update_room_settings(
                root,
                {
                    "room_id": "resident-m1",
                    "label": "AgentsAssemble",
                    "channel_settings": {
                        "lobby": {
                            "notifications": "mentions",
                            "last_read_at": "2026-06-03T10:20:30Z",
                        }
                    },
                },
            )
            updated = update_room_settings(
                root,
                {
                    "room_id": "resident-m1",
                    "topic": "updated topic",
                },
            )

        self.assertEqual(updated["settings"]["label"], "AgentsAssemble")
        self.assertEqual(updated["settings"]["topic"], "updated topic")
        self.assertEqual(
            updated["settings"]["channel_settings"]["lobby"]["last_read_at"],
            "2026-06-03T10:20:30Z",
        )

    def test_conversation_mode_defaults_to_quiet_and_round_trips(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            # Default when never set = quiet (token-cheap).
            default = update_room_settings(root, {"room_id": "resident-m1", "label": "Room"})
            self.assertEqual(default["settings"]["conversation_mode"], "quiet")
            # camelCase + case-insensitive; free/ordered round-trip.
            free = update_room_settings(root, {"room_id": "resident-m1", "conversationMode": "FREE"})
            self.assertEqual(free["settings"]["conversation_mode"], "free")
            self.assertEqual(free["settings"]["label"], "Room")  # partial update preserved
            ordered = update_room_settings(root, {"room_id": "resident-m1", "conversation_mode": "ordered"})
            self.assertEqual(ordered["settings"]["conversation_mode"], "ordered")
            # legacy "turn" maps to quiet; bad values fall back to quiet.
            legacy = update_room_settings(root, {"room_id": "resident-m1", "conversation_mode": "turn"})
            self.assertEqual(legacy["settings"]["conversation_mode"], "quiet")
            bogus = update_room_settings(root, {"room_id": "resident-m1", "conversation_mode": "chaos"})
            self.assertEqual(bogus["settings"]["conversation_mode"], "quiet")
            loaded = room_settings_payload(root, room_id="resident-m1")
        self.assertEqual(loaded["settings"]["conversation_mode"], "quiet")


if __name__ == "__main__":
    unittest.main()
