"""Explicitly migrate legacy room-global settings into canonical SQLite state."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentsassemble.legacy_room_settings_source import (
    LEGACY_ROOM_SETTINGS_SOURCE_VERSION,
    LegacyRoomSettingsSource,
    LegacyRoomSettingsSourceError,
    canonical_json,
    read_legacy_room_settings_source,
)

from agentsassemble.persistence.local.room.database import (
    ROOM_DATABASE_FILENAME,
    ROOM_SCHEMA_VERSION,
    open_room_database,
)
from agentsassemble.room.global_settings import (
    RoomGlobalSettingsRecord,
    merge_room_global_settings,
    validate_room_global_settings,
)


MIGRATION_VERSION = LEGACY_ROOM_SETTINGS_SOURCE_VERSION
PLAN_FILENAME = ".legacy-room-settings-migration-plan.json"
MIGRATION_META_KEY = "legacy_room_settings_global_fingerprint.v1"


class LegacyRoomSettingsMigrationError(RuntimeError):
    """Legacy room settings could not be moved without an explicit repair."""


@dataclass(frozen=True)
class _MigrationAnalysis:
    report: dict[str, object]
    expected_by_room: dict[str, RoomGlobalSettingsRecord]
    current_by_room: dict[str, RoomGlobalSettingsRecord]


def migrate_legacy_room_settings(
    output_root: Path,
    *,
    apply: bool = False,
) -> dict[str, object]:
    """Inspect or atomically apply legacy room-global settings.

    Dry-run writes only a migration plan. Apply requires that plan and refuses
    if either the relevant source fields or the target settings changed since
    inspection. The legacy JSON remains in place for user-preference
    compatibility; a durable database marker prevents replaying its stale
    room-global values after authority has moved.
    """

    root = Path(output_root).expanduser().resolve()
    source_path = root / "room_settings.json"
    database_path = root / "rooms" / ROOM_DATABASE_FILENAME
    plan_path = root / "rooms" / PLAN_FILENAME
    if not source_path.is_file():
        plan_path.unlink(missing_ok=True)
        return _empty_report(root, source_path, database_path, apply=apply)
    if not database_path.is_file():
        raise LegacyRoomSettingsMigrationError("Canonical SQLite room database was not found.")

    try:
        source = read_legacy_room_settings_source(source_path)
    except LegacyRoomSettingsSourceError as error:
        raise LegacyRoomSettingsMigrationError(str(error)) from error
    connection = open_room_database(database_path)
    try:
        try:
            connection.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        except sqlite3.OperationalError as error:
            raise LegacyRoomSettingsMigrationError(
                "Canonical SQLite room database is busy; stop the GUI before applying migration."
            ) from error
        _require_current_schema(connection)
        analysis = _analyze(connection, source, root=root, apply=apply)

        if not apply:
            connection.rollback()
            report = {**analysis.report, "plan_path": str(plan_path)}
            if report["status"] in {"ready", "blocked"}:
                _write_json(plan_path, report)
            else:
                plan_path.unlink(missing_ok=True)
            return report

        if analysis.report["status"] in {"not_needed", "already_applied"}:
            connection.rollback()
            plan_path.unlink(missing_ok=True)
            return {**analysis.report, "mode": "apply", "plan_path": str(plan_path)}
        if analysis.report["status"] == "blocked":
            connection.rollback()
            raise LegacyRoomSettingsMigrationError(
                "Legacy room settings require repair; run the dry-run and inspect its issues."
            )

        planned = _read_plan(plan_path)
        _verify_plan(planned, analysis.report)
        backup_dir = _backup_inputs(
            root,
            source,
            database_path=database_path,
            plan_path=plan_path,
        )
        _apply_room_settings(connection, analysis.expected_by_room)
        _verify_expected(connection, analysis.expected_by_room)
        try:
            source_after = read_legacy_room_settings_source(source_path)
        except LegacyRoomSettingsSourceError as error:
            raise LegacyRoomSettingsMigrationError(str(error)) from error
        if source_after.fingerprint != source.fingerprint:
            raise LegacyRoomSettingsMigrationError(
                "Legacy room-global settings changed during apply; "
                "canonical writes were rolled back."
            )
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES(?, ?)",
            (MIGRATION_META_KEY, source.fingerprint),
        )
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()

    try:
        _verify_committed(database_path, source.fingerprint, analysis.expected_by_room)
    except Exception as error:
        raise LegacyRoomSettingsMigrationError(
            f"Committed settings verification failed; restore the backup at {backup_dir}."
        ) from error
    plan_path.unlink(missing_ok=True)
    report = {
        **analysis.report,
        "status": "applied",
        "mode": "apply",
        "verified": True,
        "backup_dir": str(backup_dir),
        "plan_path": str(plan_path),
        "target_fingerprint": analysis.report["planned_target_fingerprint"],
    }
    _write_json(backup_dir / "applied-report.json", report)
    return report


def _analyze(
    connection: sqlite3.Connection,
    source: LegacyRoomSettingsSource,
    *,
    root: Path,
    apply: bool,
) -> _MigrationAnalysis:
    issues = list(source.issues)
    expected_by_room: dict[str, RoomGlobalSettingsRecord] = {}
    current_by_room: dict[str, RoomGlobalSettingsRecord] = {}
    rooms: list[dict[str, object]] = []
    for room_id in source.candidate_room_ids:
        room_row = connection.execute(
            "SELECT data_json FROM rooms WHERE room_id = ?",
            (room_id,),
        ).fetchone()
        settings_row = connection.execute(
            "SELECT data_json FROM room_settings WHERE room_id = ?",
            (room_id,),
        ).fetchone()
        if room_row is None:
            issues.append(
                _issue(
                    room_id,
                    "room_id",
                    "room_missing",
                    "Remove the orphan entry or create the room explicitly.",
                )
            )
            continue
        if settings_row is None:
            issues.append(
                _issue(
                    room_id,
                    "",
                    "canonical_settings_missing",
                    "Repair the canonical room settings row first.",
                )
            )
            continue
        updates = source.updates_by_room.get(room_id)
        if updates is None:
            continue
        try:
            current = validate_room_global_settings(json.loads(str(settings_row["data_json"])))
            expected = merge_room_global_settings(current, updates)
        except (json.JSONDecodeError, ValueError) as error:
            issues.append(
                _issue(room_id, "", "canonical_settings_invalid", str(error))
            )
            continue
        current_by_room[room_id] = current
        expected_by_room[room_id] = expected
        changed_fields = [
            field for field in sorted(expected) if expected[field] != current[field]
        ]
        rooms.append(
            {
                "room_id": room_id,
                "action": "update" if changed_fields else "unchanged",
                "changed_fields": changed_fields,
            }
        )

    marker_row = connection.execute(
        "SELECT value FROM schema_meta WHERE key = ?",
        (MIGRATION_META_KEY,),
    ).fetchone()
    applied_fingerprint = str(marker_row["value"] if marker_row is not None else "")
    candidate_count = source.candidate_room_count
    if issues:
        status = "blocked"
    elif candidate_count == 0:
        status = "not_needed"
    elif applied_fingerprint == source.fingerprint:
        status = "already_applied"
    else:
        status = "ready"
    current_target_fingerprint = _settings_fingerprint(current_by_room)
    planned_target_fingerprint = _settings_fingerprint(expected_by_room)
    report: dict[str, object] = {
        "version": MIGRATION_VERSION,
        "status": status,
        "mode": "apply" if apply else "dry_run",
        "output_root": str(root),
        "source_path": str(source.path),
        "database_path": str(root / "rooms" / ROOM_DATABASE_FILENAME),
        "source_fingerprint": source.fingerprint,
        "source_file_fingerprint": source.file_fingerprint,
        "applied_fingerprint": applied_fingerprint,
        "current_target_fingerprint": current_target_fingerprint,
        "planned_target_fingerprint": planned_target_fingerprint,
        "source_room_count": source.room_count,
        "candidate_room_count": candidate_count,
        "preference_only_room_count": source.preference_only_room_count,
        "change_count": sum(room["action"] == "update" for room in rooms),
        "unchanged_count": sum(room["action"] == "unchanged" for room in rooms),
        "issue_count": len(issues),
        "issues": issues,
        "rooms": rooms,
        "can_apply": status == "ready",
        "verified": status == "already_applied",
    }
    return _MigrationAnalysis(report, expected_by_room, current_by_room)


def _require_current_schema(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    version = int(row["value"]) if row is not None else 0
    if version != ROOM_SCHEMA_VERSION:
        raise LegacyRoomSettingsMigrationError(
            f"Canonical SQLite schema is version {version}; expected {ROOM_SCHEMA_VERSION}."
        )


def _read_plan(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise LegacyRoomSettingsMigrationError(
            "Run room migrate-room-settings --dry-run before --apply."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LegacyRoomSettingsMigrationError(
            "The room settings migration plan is unreadable."
        ) from error
    if not isinstance(payload, dict):
        raise LegacyRoomSettingsMigrationError("The room settings migration plan is invalid.")
    return payload


def _verify_plan(planned: dict[str, object], current: dict[str, object]) -> None:
    if planned.get("status") != "ready":
        raise LegacyRoomSettingsMigrationError(
            "The dry-run plan is blocked; repair its issues and run dry-run again."
        )
    for field in ("version", "source_fingerprint", "current_target_fingerprint"):
        if planned.get(field) != current.get(field):
            raise LegacyRoomSettingsMigrationError(
                "Legacy source or canonical target changed after dry-run; no settings were migrated."
            )


def _backup_inputs(
    root: Path,
    source: LegacyRoomSettingsSource,
    *,
    database_path: Path,
    plan_path: Path,
) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_name = f"room-settings-migration-{timestamp}-{source.fingerprint[:12]}"
    backup_dir = root / "backups" / backup_name
    backup_dir.mkdir(parents=True, exist_ok=False)
    (backup_dir / source.path.name).write_bytes(source.raw_bytes)
    (backup_dir / plan_path.name).write_bytes(plan_path.read_bytes())
    with closing(sqlite3.connect(str(database_path))) as source_db, closing(
        sqlite3.connect(str(backup_dir / database_path.name))
    ) as target_db:
        source_db.backup(target_db)
    return backup_dir


def _apply_room_settings(
    connection: sqlite3.Connection,
    expected_by_room: dict[str, RoomGlobalSettingsRecord],
) -> None:
    now = datetime.now(UTC).isoformat()
    for room_id, expected in sorted(expected_by_room.items()):
        current_row = connection.execute(
            "SELECT data_json FROM room_settings WHERE room_id = ?",
            (room_id,),
        ).fetchone()
        current = validate_room_global_settings(json.loads(str(current_row["data_json"])))
        if current == expected:
            continue
        connection.execute(
            "UPDATE room_settings SET updated_at = ?, data_json = ? WHERE room_id = ?",
            (now, canonical_json(expected), room_id),
        )
        if current["label"] != expected["label"]:
            room_row = connection.execute(
                "SELECT data_json FROM rooms WHERE room_id = ?",
                (room_id,),
            ).fetchone()
            room = json.loads(str(room_row["data_json"]))
            room["label"] = expected["label"]
            room["updated_at"] = now
            connection.execute(
                "UPDATE rooms SET label = ?, updated_at = ?, data_json = ? WHERE room_id = ?",
                (expected["label"], now, canonical_json(room), room_id),
            )


def _verify_expected(
    connection: sqlite3.Connection,
    expected_by_room: dict[str, RoomGlobalSettingsRecord],
) -> None:
    for room_id, expected in expected_by_room.items():
        row = connection.execute(
            "SELECT data_json FROM room_settings WHERE room_id = ?",
            (room_id,),
        ).fetchone()
        actual = validate_room_global_settings(json.loads(str(row["data_json"])))
        if actual != expected:
            raise LegacyRoomSettingsMigrationError(
                f"Canonical room settings verification failed for {room_id}."
            )


def _verify_committed(
    database_path: Path,
    source_fingerprint: str,
    expected_by_room: dict[str, RoomGlobalSettingsRecord],
) -> None:
    connection = open_room_database(database_path)
    try:
        marker = connection.execute(
            "SELECT value FROM schema_meta WHERE key = ?",
            (MIGRATION_META_KEY,),
        ).fetchone()
        if marker is None or str(marker["value"]) != source_fingerprint:
            raise LegacyRoomSettingsMigrationError("Migration marker verification failed.")
        _verify_expected(connection, expected_by_room)
    finally:
        connection.close()


def _settings_fingerprint(settings: dict[str, RoomGlobalSettingsRecord]) -> str:
    return hashlib.sha256(canonical_json(settings).encode("utf-8")).hexdigest()


def _issue(room_id: str, field: str, code: str, message: str) -> dict[str, str]:
    return {"room_id": room_id, "field": field, "code": code, "message": message}


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _empty_report(
    root: Path,
    source_path: Path,
    database_path: Path,
    *,
    apply: bool,
) -> dict[str, object]:
    return {
        "version": MIGRATION_VERSION,
        "status": "not_needed",
        "mode": "apply" if apply else "dry_run",
        "output_root": str(root),
        "source_path": str(source_path),
        "database_path": str(database_path),
        "source_fingerprint": "",
        "source_file_fingerprint": "",
        "applied_fingerprint": "",
        "current_target_fingerprint": _settings_fingerprint({}),
        "planned_target_fingerprint": _settings_fingerprint({}),
        "source_room_count": 0,
        "candidate_room_count": 0,
        "preference_only_room_count": 0,
        "change_count": 0,
        "unchanged_count": 0,
        "issue_count": 0,
        "issues": [],
        "rooms": [],
        "can_apply": False,
        "verified": True,
        "plan_path": str(root / "rooms" / PLAN_FILENAME),
    }
