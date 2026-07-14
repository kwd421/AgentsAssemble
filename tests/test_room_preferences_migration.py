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
from agentsassemble.identity_store import IdentityStore
from agentsassemble.room_preferences_migration import (
    PLAN_FILENAME,
    LegacyRoomPreferencesMigrationError,
    migrate_legacy_room_preferences,
)
from agentsassemble.room_store import RoomStore


class LegacyRoomPreferencesMigrationTests(unittest.TestCase):
    def test_parser_requires_user_and_defaults_to_dry_run(self) -> None:
        args = build_parser().parse_args(
            ["room", "migrate-room-preferences", "--user-id", "u-test"]
        )

        self.assertEqual(args.user_id, "u-test")
        self.assertFalse(args.apply)

    def test_cli_dispatches_dry_run_as_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            user_id, _ = self._prepare_target(root)
            self._write_source(root, {"room-a": self._legacy_preferences()})
            args = build_parser().parse_args(
                [
                    "room",
                    "migrate-room-preferences",
                    "--output-root",
                    str(root),
                    "--user-id",
                    user_id,
                    "--json",
                ]
            )
            stdout = StringIO()

            with patch("sys.stdout", stdout):
                exit_code = run_room_command(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "ready")

    def test_dry_run_then_apply_preserves_preferences_for_one_user(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            user_id, identities = self._prepare_target(root)
            other = identities.resolve_credential_user("device:other-preference-user")
            source_path = self._write_source(
                root,
                {
                    "room-a": self._legacy_preferences(
                        notifications="mute",
                        channel_settings={
                            "lobby": {
                                "notifications": "all",
                                "last_read_at": "cursor-42",
                            }
                        },
                    )
                },
            )
            original_source = source_path.read_bytes()

            dry_run = migrate_legacy_room_preferences(root, user_id=user_id)

            self.assertEqual(dry_run["status"], "ready")
            self.assertTrue((root / PLAN_FILENAME).is_file())
            self.assertEqual(
                identities.room_preferences(user_id, "room-a")["notifications"],
                "mentions",
            )

            applied = migrate_legacy_room_preferences(root, user_id=user_id, apply=True)

            migrated = identities.room_preferences(user_id, "room-a")
            self.assertEqual(applied["status"], "applied")
            self.assertTrue(applied["verified"])
            self.assertEqual(migrated["notifications"], "mute")
            self.assertEqual(
                migrated["channel_settings"]["lobby"]["last_read_at"],
                "cursor-42",
            )
            self.assertEqual(
                identities.room_preferences(other["user_id"], "room-a")["notifications"],
                "mentions",
            )
            self.assertEqual(source_path.read_bytes(), original_source)
            self.assertFalse((root / PLAN_FILENAME).exists())
            backup = Path(str(applied["backup_dir"]))
            self.assertEqual((backup / "room_settings.json").read_bytes(), original_source)
            self.assertTrue((backup / "identity.db").is_file())

            repeated = migrate_legacy_room_preferences(root, user_id=user_id)
            self.assertEqual(repeated["status"], "already_applied")

    def test_missing_user_and_orphan_room_block_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._prepare_target(root)
            self._write_source(root, {"missing-room": self._legacy_preferences()})

            report = migrate_legacy_room_preferences(root, user_id="missing-user")

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(
            {issue["code"] for issue in report["issues"]},
            {"user_missing", "room_missing"},
        )

    def test_global_only_source_does_not_require_a_preference_user(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._prepare_target(root)
            self._write_source(root, {"room-a": {"topic": "Global only"}})

            report = migrate_legacy_room_preferences(root, user_id="missing-user")

        self.assertEqual(report["status"], "not_needed")
        self.assertEqual(report["candidate_room_count"], 0)
        self.assertEqual(report["issue_count"], 0)

    def test_invalid_preference_is_reported_without_defaulting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            user_id, identities = self._prepare_target(root)
            self._write_source(
                root,
                {"room-a": self._legacy_preferences(notifications="sometimes")},
            )

            report = migrate_legacy_room_preferences(root, user_id=user_id)
            self.assertEqual(report["status"], "blocked")
            self.assertIn(
                "invalid_notifications",
                {issue["code"] for issue in report["issues"]},
            )
            self.assertEqual(
                identities.room_preferences(user_id, "room-a")["notifications"],
                "mentions",
            )

    def test_apply_refuses_target_changes_after_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            user_id, identities = self._prepare_target(root)
            self._write_source(root, {"room-a": self._legacy_preferences(notifications="all")})
            migrate_legacy_room_preferences(root, user_id=user_id)
            identities.update_room_preferences(user_id, "room-a", {"notifications": "mute"})

            with self.assertRaisesRegex(
                LegacyRoomPreferencesMigrationError,
                "changed after dry-run",
            ):
                migrate_legacy_room_preferences(root, user_id=user_id, apply=True)

            self.assertEqual(
                identities.room_preferences(user_id, "room-a")["notifications"],
                "mute",
            )

    def test_apply_refuses_source_changes_after_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            user_id, identities = self._prepare_target(root)
            self._write_source(root, {"room-a": self._legacy_preferences(notifications="all")})
            migrate_legacy_room_preferences(root, user_id=user_id)
            self._write_source(root, {"room-a": self._legacy_preferences(notifications="mute")})

            with self.assertRaisesRegex(
                LegacyRoomPreferencesMigrationError,
                "changed after dry-run",
            ):
                migrate_legacy_room_preferences(root, user_id=user_id, apply=True)

            self.assertEqual(
                identities.room_preferences(user_id, "room-a")["notifications"],
                "mentions",
            )

    def test_verification_failure_rolls_back_preferences_and_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            user_id, identities = self._prepare_target(root)
            self._write_source(root, {"room-a": self._legacy_preferences(notifications="all")})
            migrate_legacy_room_preferences(root, user_id=user_id)

            with patch(
                "agentsassemble.room_preferences_migration._verify_expected",
                side_effect=LegacyRoomPreferencesMigrationError("injected failure"),
            ), self.assertRaisesRegex(LegacyRoomPreferencesMigrationError, "injected"):
                migrate_legacy_room_preferences(root, user_id=user_id, apply=True)

            self.assertEqual(
                identities.room_preferences(user_id, "room-a")["notifications"],
                "mentions",
            )
            with closing(sqlite3.connect(identities.db_path)) as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM legacy_room_preference_migrations"
                ).fetchone()[0]
            self.assertEqual(count, 0)

    def test_committed_verification_failure_reports_backup_location(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            user_id, identities = self._prepare_target(root)
            self._write_source(root, {"room-a": self._legacy_preferences(notifications="all")})
            migrate_legacy_room_preferences(root, user_id=user_id)

            with patch(
                "agentsassemble.room_preferences_migration._verify_committed",
                side_effect=RuntimeError("injected post-commit failure"),
            ), self.assertRaisesRegex(
                LegacyRoomPreferencesMigrationError,
                r"restore the backup at .+room-preferences-migration-",
            ):
                migrate_legacy_room_preferences(root, user_id=user_id, apply=True)

            self.assertEqual(
                identities.room_preferences(user_id, "room-a")["notifications"],
                "all",
            )
            backups = list((root / "backups").glob("room-preferences-migration-*"))
            self.assertEqual(len(backups), 1)
            self.assertTrue((backups[0] / "identity.db").is_file())

    @staticmethod
    def _prepare_target(root: Path) -> tuple[str, IdentityStore]:
        RoomStore(root).create_room("room-a", label="Room A")
        identities = IdentityStore(root / "identity.db")
        user = identities.resolve_credential_user("device:preference-owner")
        return str(user["user_id"]), identities

    @staticmethod
    def _write_source(root: Path, rooms: dict[str, object]) -> Path:
        path = root / "room_settings.json"
        path.write_text(
            json.dumps({"rooms": rooms}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _legacy_preferences(
        *,
        notifications: object = "mentions",
        channel_settings: object | None = None,
    ) -> dict[str, object]:
        return {
            "appearance": {"notifications": notifications},
            "channel_settings": channel_settings if channel_settings is not None else {},
        }


if __name__ == "__main__":
    unittest.main()
