from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.room.channels import (
    MAX_CHANNELS_PER_ROOM,
    ChannelError,
    add_channel,
    clean_channels,
    find_channel,
    remove_channel,
    rename_channel,
    reorder_channels,
)
from agentsassemble.room.settings import room_settings_payload, update_room_settings


class RoomChannelModelTests(unittest.TestCase):
    def test_add_normalizes_name_and_type_and_positions(self):
        channels, first = add_channel(
            [], name="  구현방  ", channel_type="text",
            channel_id="c00000000001", created_at="2026-01-01T00:00:00Z",
        )
        channels, second = add_channel(
            channels, name="음성 라운지", channel_type="VOICE",
            channel_id="c00000000002", created_at="2026-01-02T00:00:00Z",
        )
        self.assertEqual(first["name"], "구현방")  # whitespace collapsed/trimmed
        self.assertEqual(first["type"], "text")
        self.assertEqual(first["position"], 0)
        self.assertEqual(second["type"], "voice")  # case-insensitive type
        self.assertEqual(second["position"], 1)

    def test_add_rejects_empty_name_and_full_room(self):
        with self.assertRaises(ChannelError) as ctx:
            add_channel([], name="   ")
        self.assertEqual(ctx.exception.category, "name")

        full = []
        for index in range(MAX_CHANNELS_PER_ROOM):
            full, _ = add_channel(full, name=f"c{index}", channel_id=f"c{index:012d}")
        with self.assertRaises(ChannelError) as ctx:
            add_channel(full, name="one too many")
        self.assertEqual(ctx.exception.category, "limit")

    def test_generated_ids_are_opaque_and_unique(self):
        channels, a = add_channel([], name="a")
        channels, b = add_channel(channels, name="b")
        self.assertNotEqual(a["id"], b["id"])
        for channel in (a, b):
            self.assertTrue(channel["id"].startswith("c"))
            self.assertEqual(len(channel["id"]), 13)  # "c" + 12 hex

    def test_rename_keeps_id_and_stream(self):
        channels, channel = add_channel([], name="old", channel_id="c00000000001")
        channels = rename_channel(channels, "c00000000001", "  new name  ")
        self.assertEqual(find_channel(channels, "c00000000001")["name"], "new name")
        with self.assertRaises(ChannelError) as ctx:
            rename_channel(channels, "nope", "x")
        self.assertEqual(ctx.exception.category, "not_found")
        with self.assertRaises(ChannelError) as ctx:
            rename_channel(channels, "c00000000001", "")
        self.assertEqual(ctx.exception.category, "name")

    def test_remove_repacks_positions(self):
        channels = []
        for index in range(3):
            channels, _ = add_channel(channels, name=f"c{index}", channel_id=f"c{index:012d}")
        channels = remove_channel(channels, "c000000000000")  # remove the first
        self.assertEqual([c["id"] for c in channels], ["c000000000001", "c000000000002"])
        self.assertEqual([c["position"] for c in channels], [0, 1])
        with self.assertRaises(ChannelError) as ctx:
            remove_channel(channels, "ghost")
        self.assertEqual(ctx.exception.category, "not_found")

    def test_reorder_leads_named_then_keeps_rest(self):
        channels = []
        for index in range(3):
            channels, _ = add_channel(channels, name=f"c{index}", channel_id=f"c{index:012d}")
        # name only the last id; the unnamed two keep their relative order after it
        channels = reorder_channels(channels, ["c000000000002"])
        self.assertEqual(
            [c["id"] for c in channels],
            ["c000000000002", "c000000000000", "c000000000001"],
        )
        self.assertEqual([c["position"] for c in channels], [0, 1, 2])

    def test_clean_channels_drops_bad_dedups_and_sorts(self):
        cleaned = clean_channels([
            {"id": "c1", "name": "keep", "type": "text", "position": 5, "created_at": "2026-01-01T00:00:00Z"},
            {"id": "c1", "name": "dup id ignored", "type": "voice", "position": 0},
            {"name": "no id dropped"},
            {"id": "c2", "name": "", "position": 0},  # no name dropped
            {"id": "c3", "name": "earlier", "type": "voice", "position": 1, "created_at": "2026-01-01T00:00:00Z"},
            "not a dict",
        ])
        self.assertEqual([c["id"] for c in cleaned], ["c3", "c1"])  # sorted by position then created_at
        self.assertEqual([c["position"] for c in cleaned], [0, 1])  # repacked dense


class RoomChannelPersistenceTests(unittest.TestCase):
    def test_channels_round_trip_and_default_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            channels, _ = add_channel(
                [], name="구현방", channel_type="text",
                channel_id="c00000000001", created_at="2026-01-01T00:00:00Z",
            )
            saved = update_room_settings(root, {"room_id": "r1", "channels": channels})
            self.assertEqual(saved["settings"]["channels"][0]["name"], "구현방")
            self.assertEqual(saved["settings"]["channels"][0]["type"], "text")
            loaded = room_settings_payload(root, room_id="r1")
            self.assertEqual(loaded["settings"]["channels"][0]["id"], "c00000000001")
            # a room that never set channels reports an empty list, not missing.
            fresh = update_room_settings(root, {"room_id": "r2", "label": "x"})
            self.assertEqual(fresh["settings"]["channels"], [])

    def test_partial_update_preserves_channels(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            channels, _ = add_channel([], name="음성", channel_type="voice", channel_id="c00000000009")
            update_room_settings(root, {"room_id": "r1", "channels": channels})
            updated = update_room_settings(root, {"room_id": "r1", "topic": "새 주제"})
            self.assertEqual(updated["settings"]["topic"], "새 주제")
            self.assertEqual(updated["settings"]["channels"][0]["id"], "c00000000009")


if __name__ == "__main__":
    unittest.main()
