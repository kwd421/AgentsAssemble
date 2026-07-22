"""Execution for retained room data migration CLI commands."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def run_legacy_room_command(args: argparse.Namespace) -> int | None:
    """Run a retained migration command, or return ``None`` when not handled."""
    if args.room_command == "migrate-legacy-messages":
        from agentsassemble.legacy.room.migration import migrate_legacy_messages

        try:
            result = migrate_legacy_messages(Path(args.output_root), apply=bool(args.apply))
        except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(
                f"{result['status']}: {result['message_count']} message(s) "
                f"from {result['room_count']} room(s)"
            )
            for room in result["rooms"]:
                print(f"- {room['room_id']}: {room['message_count']}")
            if result.get("backup_dir"):
                print(f"backup: {result['backup_dir']}")
        return 0

    if args.room_command == "migrate-postgres":
        from agentsassemble.application.room_repository_factory import (
            RoomRepositoryConfigurationError,
            RoomRepositorySettings,
        )
        from agentsassemble.legacy.room.repository_migration import (
            RoomRepositoryTransferError,
            migrate_sqlite_rooms_to_postgres,
        )

        try:
            settings = RoomRepositorySettings.from_environment(
                backend="postgresql",
                postgres_dsn_env=str(args.postgres_dsn_env),
            )
            if not settings.postgres_dsn:
                raise RoomRepositoryConfigurationError(
                    f"PostgreSQL room migration requires {settings.postgres_dsn_env} to be set."
                )
            result = migrate_sqlite_rooms_to_postgres(
                Path(args.output_root),
                postgres_dsn=settings.postgres_dsn,
                apply=bool(args.apply),
            )
        except (RoomRepositoryConfigurationError, RoomRepositoryTransferError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            source_counts = result["source"]["row_counts"]
            print(
                f"PostgreSQL room migration {result['mode']}: {result['status']} · "
                f"rooms={source_counts['rooms']} · events={source_counts['room_events']}"
            )
            print(f"source checksum: {result['source']['checksum']}")
            if result.get("verified"):
                print("target checksum verified")
            elif not result.get("can_apply"):
                print("target is not safe to apply")
        return 0 if result.get("status") in {"ready", "applied"} else 1

    if args.room_command == "migrate-room-settings":
        from agentsassemble.legacy.room.settings_migration import (
            LegacyRoomSettingsMigrationError,
            migrate_legacy_room_settings,
        )

        try:
            result = migrate_legacy_room_settings(
                Path(args.output_root),
                apply=bool(args.apply),
            )
        except (LegacyRoomSettingsMigrationError, OSError, ValueError, sqlite3.Error) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(
                f"Legacy room settings migration {result['mode']}: {result['status']} · "
                f"rooms={result['candidate_room_count']} · changes={result['change_count']} · "
                f"issues={result['issue_count']}"
            )
            if result.get("source_fingerprint"):
                print(f"source fingerprint: {result['source_fingerprint']}")
            for issue in result.get("issues", []):
                print(
                    f"- {issue.get('room_id') or '<file>'} {issue.get('field') or '<record>'}: "
                    f"{issue.get('message')}"
                )
            if result.get("backup_dir"):
                print(f"backup: {result['backup_dir']}")
            elif result.get("status") == "ready":
                print("Run the same command with --apply after reviewing the dry-run plan.")
        return 0 if result.get("status") in {"ready", "applied", "already_applied", "not_needed"} else 1

    if args.room_command == "migrate-room-preferences":
        from agentsassemble.legacy.room.preferences_migration import (
            LegacyRoomPreferencesMigrationError,
            migrate_legacy_room_preferences,
        )

        try:
            result = migrate_legacy_room_preferences(
                Path(args.output_root),
                user_id=str(args.user_id),
                apply=bool(args.apply),
            )
        except (LegacyRoomPreferencesMigrationError, OSError, ValueError, sqlite3.Error) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(
                f"Legacy room preference migration {result['mode']}: {result['status']} · "
                f"user={result['user_id']} · rooms={result['candidate_room_count']} · "
                f"changes={result['change_count']} · issues={result['issue_count']}"
            )
            for issue in result.get("issues", []):
                print(
                    f"- {issue.get('room_id') or '<file>'} {issue.get('field') or '<record>'}: "
                    f"{issue.get('message')}"
                )
            if result.get("backup_dir"):
                print(f"backup: {result['backup_dir']}")
            elif result.get("status") == "ready":
                print("Run the same command with --apply after reviewing the dry-run plan.")
        return 0 if result.get("status") in {"ready", "applied", "already_applied", "not_needed"} else 1

    return None
