from __future__ import annotations

import unittest

from agentsassemble.room.user_preferences import (
    default_room_user_preferences,
    merge_room_user_preferences,
    validate_room_user_preferences,
)


class RoomUserPreferencesTests(unittest.TestCase):
    def test_defaults_are_complete_and_independent(self) -> None:
        first = default_room_user_preferences()
        second = default_room_user_preferences()

        first["channel_settings"]["lobby"] = {
            "notifications": "all",
            "last_read_at": "cursor-1",
        }

        self.assertEqual(second, {"notifications": "mentions", "channel_settings": {}})

    def test_merge_accepts_builtin_and_custom_channel_preferences(self) -> None:
        updated = merge_room_user_preferences(
            default_room_user_preferences(),
            {
                "notifications": "mute",
                "channel_settings": {
                    "lobby": {"notifications": "mentions", "last_read_at": "cursor-1"},
                    "c0123456789ab": {
                        "notifications": "all",
                        "last_read_at": "cursor-2",
                    },
                },
            },
        )

        self.assertEqual(updated["notifications"], "mute")
        self.assertEqual(updated["channel_settings"]["c0123456789ab"]["last_read_at"], "cursor-2")

    def test_invalid_values_fail_instead_of_defaulting(self) -> None:
        invalid_records = (
            {"notifications": "sometimes", "channel_settings": {}},
            {
                "notifications": "mentions",
                "channel_settings": {
                    "unknown": {"notifications": "all", "last_read_at": ""}
                },
            },
            {
                "notifications": "mentions",
                "channel_settings": {
                    "lobby": {"notifications": "sometimes", "last_read_at": ""}
                },
            },
            {
                "notifications": "mentions",
                "channel_settings": {
                    "lobby": {"notifications": "all", "last_read_at": "bad\nvalue"}
                },
            },
        )

        for record in invalid_records:
            with self.subTest(record=record), self.assertRaises(ValueError):
                validate_room_user_preferences(record)


if __name__ == "__main__":
    unittest.main()
