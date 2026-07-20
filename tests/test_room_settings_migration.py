from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from agentsassemble.cli import build_parser, run_room_command
from agentsassemble.legacy.room.settings_migration import (
    MIGRATION_META_KEY,
    PLAN_FILENAME,
    LegacyRoomSettingsMigrationError,
    migrate_legacy_room_settings,
)
from agentsassemble.room_store import RoomStore


class LegacyRoomSettingsMigrationTests(unittest.TestCase):
    def test_parser_defaults_to_dry_run(self) -> None:
        args = build_parser().parse_args(["room", "migrate-room-settings"])

        self.assertFalse(args.apply)
        self.assertEqual(args.output_root, ".agentsassemble")

    def test_cli_dispatches_dry_run_and_returns_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            RoomStore(root).create_room("general", label="General")
            self._write_source(root, {"general": self._legacy_settings(label="Legacy")})
            args = build_parser().parse_args(
                [
                    "room",
                    "migrate-room-settings",
                    "--output-root",
                    str(root),
                    "--json",
                ]
            )
            stdout = StringIO()

            with patch("sys.stdout", stdout):
                exit_code = run_room_command(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "ready")

    def test_dry_run_then_apply_backs_up_and_verifies_global_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("general", label="Current")
            source_path = self._write_source(
                root,
                {
                    "general": self._legacy_settings(
                        label="Legacy Room",
                        topic="Migrated topic",
                        short_label="LG",
                        conversation_mode="continuous",
                        max_relay_turns=4,
                    )
                },
            )
            original_source = source_path.read_bytes()

            dry_run = migrate_legacy_room_settings(root)

            self.assertEqual(dry_run["status"], "ready")
            self.assertEqual(dry_run["change_count"], 1)
            self.assertTrue(dry_run["source_fingerprint"])
            self.assertTrue((root / "rooms" / PLAN_FILENAME).is_file())
            self.assertEqual(store.room_settings("general")["label"], "Current")

            applied = migrate_legacy_room_settings(root, apply=True)

            settings = store.room_settings("general")
            self.assertEqual(applied["status"], "applied")
            self.assertTrue(applied["verified"])
            self.assertEqual(settings["label"], "Legacy Room")
            self.assertEqual(settings["topic"], "Migrated topic")
            self.assertEqual(settings["appearance"]["banner_preset"], "forest")
            self.assertEqual(settings["appearance"]["icon_label"], "LG")
            self.assertEqual(settings["appearance"]["invite_scope"], "read_only")
            self.assertEqual(settings["conversation_mode"], "continuous")
            self.assertEqual(settings["max_relay_turns"], 4)
            self.assertEqual(store.room("general")["label"], "Legacy Room")
            self.assertEqual(source_path.read_bytes(), original_source)
            self.assertFalse((root / "rooms" / PLAN_FILENAME).exists())

            backup_dir = Path(str(applied["backup_dir"]))
            self.assertEqual((backup_dir / "room_settings.json").read_bytes(), original_source)
            self.assertTrue((backup_dir / "rooms.sqlite3").is_file())
            with closing(sqlite3.connect(backup_dir / "rooms.sqlite3")) as backup:
                row = backup.execute(
                    "SELECT data_json FROM room_settings WHERE room_id = 'general'"
                ).fetchone()
            self.assertEqual(json.loads(str(row[0]))["label"], "Current")

            already_applied = migrate_legacy_room_settings(root)
            self.assertEqual(already_applied["status"], "already_applied")
            store.update_room_settings("general", {"topic": "A newer canonical edit"})
            still_applied = migrate_legacy_room_settings(root)
            self.assertEqual(still_applied["status"], "already_applied")
            self.assertEqual(store.room_settings("general")["topic"], "A newer canonical edit")

    def test_invalid_mode_and_relay_are_reported_without_defaulting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("general", label="General")
            self._write_source(
                root,
                {
                    "general": self._legacy_settings(
                        conversation_mode="free",
                        max_relay_turns="6",
                    )
                },
            )

            report = migrate_legacy_room_settings(root)

            self.assertEqual(report["status"], "blocked")
            codes = {issue["code"] for issue in report["issues"]}
            self.assertIn("invalid_conversation_mode", codes)
            self.assertIn("invalid_max_relay_turns", codes)
            with self.assertRaisesRegex(LegacyRoomSettingsMigrationError, "require repair"):
                migrate_legacy_room_settings(root, apply=True)
            self.assertEqual(store.room_settings("general")["conversation_mode"], "ordered")
            self.assertEqual(store.room_settings("general")["max_relay_turns"], 6)

    def test_apply_refuses_when_source_changed_after_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("general", label="Current")
            self._write_source(root, {"general": self._legacy_settings(label="First")})
            migrate_legacy_room_settings(root)
            self._write_source(root, {"general": self._legacy_settings(label="Second")})

            with self.assertRaisesRegex(LegacyRoomSettingsMigrationError, "changed after dry-run"):
                migrate_legacy_room_settings(root, apply=True)

            self.assertEqual(store.room_settings("general")["label"], "Current")
            self.assertFalse((root / "backups").exists())

    def test_preference_change_does_not_invalidate_global_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("general", label="Current")
            settings = self._legacy_settings(label="Legacy")
            self._write_source(root, {"general": settings})
            dry_run = migrate_legacy_room_settings(root)
            settings["appearance"]["notifications"] = "mute"
            self._write_source(root, {"general": settings})

            applied = migrate_legacy_room_settings(root, apply=True)

            self.assertEqual(applied["status"], "applied")
            self.assertEqual(applied["source_fingerprint"], dry_run["source_fingerprint"])
            self.assertEqual(store.room_settings("general")["label"], "Legacy")

    def test_apply_refuses_when_target_changed_after_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("general", label="Current")
            self._write_source(root, {"general": self._legacy_settings(label="Legacy")})
            migrate_legacy_room_settings(root)
            store.update_room_settings("general", {"topic": "Concurrent edit"})

            with self.assertRaisesRegex(LegacyRoomSettingsMigrationError, "changed after dry-run"):
                migrate_legacy_room_settings(root, apply=True)

            self.assertEqual(store.room_settings("general")["topic"], "Concurrent edit")
            self.assertEqual(store.room_settings("general")["label"], "Current")

    def test_orphan_room_is_a_blocking_repair_issue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            RoomStore(root).create_room("general", label="General")
            self._write_source(root, {"missing": self._legacy_settings(label="Missing")})

            report = migrate_legacy_room_settings(root)

            self.assertEqual(report["status"], "blocked")
            self.assertIn("room_missing", {issue["code"] for issue in report["issues"]})

    def test_preference_only_file_does_not_need_global_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            RoomStore(root).create_room("general", label="General")
            self._write_source(
                root,
                {
                    "general": {
                        "room_id": "general",
                        "appearance": {"notifications": "mentions"},
                        "channel_settings": {"lobby": {"notifications": "all"}},
                    }
                },
            )

            report = migrate_legacy_room_settings(root)

            self.assertEqual(report["status"], "not_needed")
            self.assertEqual(report["candidate_room_count"], 0)
            self.assertEqual(report["preference_only_room_count"], 1)

    def test_failure_after_writes_rolls_back_every_room_and_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = RoomStore(root)
            store.create_room("one", label="One")
            store.create_room("two", label="Two")
            self._write_source(
                root,
                {
                    "one": self._legacy_settings(label="Changed One"),
                    "two": self._legacy_settings(label="Changed Two"),
                },
            )
            migrate_legacy_room_settings(root)

            with patch(
                "agentsassemble.legacy.room.settings_migration._verify_expected",
                side_effect=LegacyRoomSettingsMigrationError("injected verification failure"),
            ), self.assertRaisesRegex(LegacyRoomSettingsMigrationError, "injected"):
                migrate_legacy_room_settings(root, apply=True)

            self.assertEqual(store.room_settings("one")["label"], "One")
            self.assertEqual(store.room_settings("two")["label"], "Two")
            with closing(sqlite3.connect(store.database_path)) as connection:
                marker = connection.execute(
                    "SELECT value FROM schema_meta WHERE key = ?",
                    (MIGRATION_META_KEY,),
                ).fetchone()
            self.assertIsNone(marker)
            self.assertEqual(len(list((root / "backups").glob("room-settings-migration-*"))), 1)

    def test_malformed_source_is_an_explicit_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            RoomStore(root).create_room("general", label="General")
            (root / "room_settings.json").write_text("{broken", encoding="utf-8")

            with self.assertRaisesRegex(LegacyRoomSettingsMigrationError, "malformed"):
                migrate_legacy_room_settings(root)

    @staticmethod
    def _write_source(root: Path, rooms: dict[str, object]) -> Path:
        path = root / "room_settings.json"
        path.write_text(
            json.dumps({"rooms": rooms}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _legacy_settings(
        *,
        label: object = "Legacy",
        topic: object = "",
        short_label: object = "",
        conversation_mode: object = "ordered",
        max_relay_turns: object = 6,
    ) -> dict[str, object]:
        return {
            "label": label,
            "topic": topic,
            "short_label": short_label,
            "appearance": {
                "banner_preset": "forest",
                "banner_image_url": "",
                "icon_image_url": "",
                "icon_label": "",
                "invite_scope": "read_only",
                "notifications": "all",
            },
            "conversation_mode": conversation_mode,
            "max_relay_turns": max_relay_turns,
            "channels": [],
            "member_roles": {},
            "channel_settings": {},
        }


if __name__ == "__main__":
    unittest.main()
