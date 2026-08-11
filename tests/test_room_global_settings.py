from __future__ import annotations

import unittest

from agentsassemble.room.global_settings import (
    ROOM_GLOBAL_SETTING_FIELDS,
    default_room_global_settings,
    merge_room_global_settings,
    validate_room_global_settings,
)


def _settings() -> dict[str, object]:
    return {
        "label": "General",
        "topic": "Shared room",
        "appearance": {
            "banner_preset": "forest",
            "banner_image_url": "",
            "icon_image_url": "/api/attachments/Abcdefgh1234?view=1",
            "icon_label": "AA",
            "invite_scope": "room",
        },
        "conversation_mode": "ambient",
        "tool_mode": "tabletop",
        "ordered_exclude_previous_speaker": False,
        "max_relay_turns": 4,
        "channels": [
            {
                "id": "c000000000001",
                "name": "workroom",
                "type": "text",
                "position": 0,
                "created_at": "2026-07-14T00:00:00+00:00",
            }
        ],
        "activity_plugin": "rimworld",
    }


class RoomGlobalSettingsTests(unittest.TestCase):
    def test_default_record_has_only_room_global_fields(self) -> None:
        settings = default_room_global_settings(label="General")

        self.assertEqual(set(settings), ROOM_GLOBAL_SETTING_FIELDS)
        self.assertEqual(settings["label"], "General")
        self.assertEqual(settings["conversation_mode"], "ordered")
        self.assertEqual(settings["tool_mode"], "chat")
        self.assertEqual(settings["activity_plugin"], "")
        self.assertTrue(settings["ordered_exclude_previous_speaker"])
        self.assertEqual(settings["max_relay_turns"], 6)
        self.assertEqual(settings["channels"], [])
        self.assertNotIn("notifications", settings["appearance"])

    def test_validation_preserves_complete_canonical_record(self) -> None:
        source = _settings()

        validated = validate_room_global_settings(source)

        self.assertEqual(validated, source)
        self.assertIsNot(validated, source)
        self.assertIsNot(validated["appearance"], source["appearance"])
        self.assertIsNot(validated["channels"], source["channels"])

    def test_user_preferences_and_participant_roles_are_rejected(self) -> None:
        for field, value in (
            ("notifications", "mute"),
            ("last_read_at", "2026-07-14T00:00:00Z"),
            ("channel_settings", {"lobby": {"notifications": "all"}}),
            ("member_roles", {"agent-a": "reviewer"}),
        ):
            with self.subTest(field=field):
                source = {**_settings(), field: value}
                with self.assertRaisesRegex(ValueError, "Unsupported room settings fields"):
                    validate_room_global_settings(source)

        source = _settings()
        source["appearance"] = {**source["appearance"], "notifications": "mute"}
        with self.assertRaisesRegex(ValueError, "Unsupported appearance fields"):
            validate_room_global_settings(source)

    def test_invalid_mode_and_relay_are_not_silently_replaced(self) -> None:
        source = _settings()
        for invalid in ("free", "turn", "chaos", "AMBIENT", ""):
            with self.subTest(mode=invalid):
                with self.assertRaisesRegex(ValueError, "Unsupported conversation_mode"):
                    validate_room_global_settings({**source, "conversation_mode": invalid})

        for invalid in (1, 21, "6", True, None):
            with self.subTest(relay=invalid):
                with self.assertRaisesRegex(ValueError, "max_relay_turns"):
                    validate_room_global_settings({**source, "max_relay_turns": invalid})

        for invalid in ("dnd", "general", "TABLETOP", "", None):
            with self.subTest(tool_mode=invalid):
                with self.assertRaisesRegex(ValueError, "tool_mode"):
                    validate_room_global_settings({**source, "tool_mode": invalid})

        for invalid in (1, 0, "true", None):
            with self.subTest(exclude_previous=invalid):
                with self.assertRaisesRegex(
                    ValueError,
                    "ordered_exclude_previous_speaker",
                ):
                    validate_room_global_settings(
                        {
                            **source,
                            "ordered_exclude_previous_speaker": invalid,
                        }
                    )

        with self.assertRaisesRegex(ValueError, "Unknown first-party"):
            validate_room_global_settings(
                {**source, "activity_plugin": "marketplace-mod"}
            )

    def test_channels_must_be_canonical_and_ordered(self) -> None:
        source = _settings()
        channel = dict(source["channels"][0])

        for updates in (
            {"id": "not-an-id"},
            {"name": "  workroom  "},
            {"type": "stage"},
            {"position": 2},
        ):
            with self.subTest(updates=updates):
                with self.assertRaises(ValueError):
                    validate_room_global_settings(
                        {**source, "channels": [{**channel, **updates}]}
                    )

    def test_partial_merge_revalidates_the_complete_record(self) -> None:
        current = _settings()

        updated = merge_room_global_settings(
            current,
            {
                "topic": "Updated",
                "conversation_mode": "ordered",
                "ordered_exclude_previous_speaker": True,
                "appearance": {"banner_preset": "ember"},
            },
        )

        self.assertEqual(updated["topic"], "Updated")
        self.assertEqual(updated["conversation_mode"], "ordered")
        self.assertTrue(updated["ordered_exclude_previous_speaker"])
        self.assertEqual(updated["appearance"]["banner_preset"], "ember")
        self.assertEqual(updated["appearance"]["icon_label"], "AA")
        self.assertEqual(updated["channels"], current["channels"])
        with self.assertRaisesRegex(ValueError, "Unsupported room settings fields"):
            merge_room_global_settings(current, {"last_read_at": "now"})


if __name__ == "__main__":
    unittest.main()
