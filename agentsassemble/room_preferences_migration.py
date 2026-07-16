"""Explicitly migrate legacy room preferences to one identity user."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentsassemble.identity_room_preferences import (
    ROOM_PREFERENCE_MIGRATIONS_TABLE,
    encode_room_preferences,
    read_room_preferences,
    update_room_preferences,
)
from agentsassemble.identity_store import IDENTITY_DB_FILENAME
from agentsassemble.legacy_room_preferences_source import (
    LEGACY_ROOM_PREFERENCES_SOURCE_VERSION,
    LegacyRoomPreferencesSource,
    read_legacy_room_preferences_source,
)
from agentsassemble.legacy_room_settings_document import LegacyRoomSettingsSourceError
from agentsassemble.persistence.local.room.database import ROOM_DATABASE_FILENAME, open_room_database
from agentsassemble.room_user_preferences import RoomUserPreferencesRecord
from agentsassemble.room_user_preferences import merge_room_user_preferences


MIGRATION_VERSION = LEGACY_ROOM_PREFERENCES_SOURCE_VERSION
PLAN_FILENAME = ".legacy-room-preferences-migration-plan.json"
MIGRATION_TABLE = ROOM_PREFERENCE_MIGRATIONS_TABLE


class LegacyRoomPreferencesMigrationError(RuntimeError):
    """Legacy room preferences cannot be assigned safely to the requested user."""


@dataclass(frozen=True)
class _MigrationAnalysis:
    report: dict[str, object]
    expected_by_room: dict[str, RoomUserPreferencesRecord]


def migrate_legacy_room_preferences(
    output_root: Path,
    *,
    user_id: str,
    apply: bool = False,
) -> dict[str, object]:
    root = Path(output_root).expanduser().resolve()
    source_path = root / "room_settings.json"
    identity_path = root / IDENTITY_DB_FILENAME
    room_database_path = root / "rooms" / ROOM_DATABASE_FILENAME
    plan_path = root / PLAN_FILENAME
    if not source_path.is_file():
        plan_path.unlink(missing_ok=True)
        return _empty_report(
            root,
            source_path,
            identity_path,
            user_id=user_id,
            apply=apply,
        )
    if not identity_path.is_file():
        raise LegacyRoomPreferencesMigrationError("Identity database was not found.")
    if not room_database_path.is_file():
        raise LegacyRoomPreferencesMigrationError("Canonical room database was not found.")

    try:
        source = read_legacy_room_preferences_source(source_path)
    except LegacyRoomSettingsSourceError as error:
        raise LegacyRoomPreferencesMigrationError(str(error)) from error

    identity = _open_identity_database(identity_path)
    rooms = open_room_database(room_database_path)
    try:
        try:
            identity.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
            rooms.execute("BEGIN")
        except sqlite3.OperationalError as error:
            raise LegacyRoomPreferencesMigrationError(
                "Room or identity database is busy; stop the GUI before applying migration."
            ) from error
        _require_preference_schema(identity)
        analysis = _analyze(identity, rooms, source, root=root, user_id=user_id, apply=apply)

        if not apply:
            identity.rollback()
            rooms.rollback()
            report = {**analysis.report, "plan_path": str(plan_path)}
            if report["status"] in {"ready", "blocked"}:
                _write_json(plan_path, report)
            else:
                plan_path.unlink(missing_ok=True)
            return report

        if analysis.report["status"] in {"not_needed", "already_applied"}:
            identity.rollback()
            rooms.rollback()
            plan_path.unlink(missing_ok=True)
            return {**analysis.report, "mode": "apply", "plan_path": str(plan_path)}
        if analysis.report["status"] == "blocked":
            raise LegacyRoomPreferencesMigrationError(
                "Legacy room preferences require repair; inspect a new dry-run plan."
            )

        planned = _read_plan(plan_path)
        _verify_plan(planned, analysis.report)
        backup_dir = _backup_inputs(
            root,
            source,
            identity_path=identity_path,
            plan_path=plan_path,
        )
        now = datetime.now(UTC).isoformat()
        for room_id, expected in sorted(analysis.expected_by_room.items()):
            update_room_preferences(
                identity,
                user_id,
                room_id,
                expected,
                now=now,
            )
        _verify_expected(identity, user_id, analysis.expected_by_room)
        try:
            source_after = read_legacy_room_preferences_source(source_path)
        except LegacyRoomSettingsSourceError as error:
            raise LegacyRoomPreferencesMigrationError(str(error)) from error
        if source_after.fingerprint != source.fingerprint:
            raise LegacyRoomPreferencesMigrationError(
                "Legacy preference fields changed during apply; writes were rolled back."
            )
        identity.execute(
            f"INSERT INTO {MIGRATION_TABLE}"
            " (user_id, source_fingerprint, applied_at) VALUES (?, ?, ?)",
            (user_id, source.fingerprint, now),
        )
        identity.commit()
        rooms.rollback()
    except Exception:
        if identity.in_transaction:
            identity.rollback()
        if rooms.in_transaction:
            rooms.rollback()
        raise
    finally:
        identity.close()
        rooms.close()

    try:
        _verify_committed(identity_path, user_id, source.fingerprint, analysis.expected_by_room)
    except Exception as error:
        raise LegacyRoomPreferencesMigrationError(
            f"Committed preference verification failed; restore the backup at {backup_dir}."
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
    identity: sqlite3.Connection,
    rooms: sqlite3.Connection,
    source: LegacyRoomPreferencesSource,
    *,
    root: Path,
    user_id: str,
    apply: bool,
) -> _MigrationAnalysis:
    issues = list(source.issues)
    expected_by_room: dict[str, RoomUserPreferencesRecord] = {}
    current_by_room: dict[str, RoomUserPreferencesRecord] = {}
    user = None
    if source.candidate_room_count:
        user = identity.execute(
            "SELECT 1 FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if source.candidate_room_count and user is None:
        issues.append(_issue("", "user_id", "user_missing", "Choose an existing identity user."))

    room_reports: list[dict[str, object]] = []
    for room_id in source.candidate_room_ids:
        if rooms.execute(
            "SELECT 1 FROM rooms WHERE room_id = ?",
            (room_id,),
        ).fetchone() is None:
            issues.append(
                _issue(
                    room_id,
                    "room_id",
                    "room_missing",
                    "Remove the orphan preference entry or restore the room explicitly.",
                )
            )
            continue
        updates = source.updates_by_room.get(room_id)
        if updates is None or user is None:
            continue
        try:
            current = read_room_preferences(identity, user_id, room_id)
            expected = merge_room_user_preferences(current, updates)
        except ValueError as error:
            issues.append(_issue(room_id, "", "target_preferences_invalid", str(error)))
            continue
        current_by_room[room_id] = current
        expected_by_room[room_id] = expected
        room_reports.append(
            {
                "room_id": room_id,
                "action": "update" if current != expected else "unchanged",
            }
        )

    marker = identity.execute(
        f"SELECT 1 FROM {MIGRATION_TABLE}"
        " WHERE user_id = ? AND source_fingerprint = ?",
        (user_id, source.fingerprint),
    ).fetchone()
    if issues:
        status = "blocked"
    elif source.candidate_room_count == 0:
        status = "not_needed"
    elif marker is not None:
        status = "already_applied"
    else:
        status = "ready"
    report: dict[str, object] = {
        "version": MIGRATION_VERSION,
        "status": status,
        "mode": "apply" if apply else "dry_run",
        "output_root": str(root),
        "user_id": user_id,
        "source_path": str(source.path),
        "identity_database_path": str(root / IDENTITY_DB_FILENAME),
        "source_fingerprint": source.fingerprint,
        "source_file_fingerprint": source.file_fingerprint,
        "current_target_fingerprint": _preferences_fingerprint(current_by_room),
        "planned_target_fingerprint": _preferences_fingerprint(expected_by_room),
        "source_room_count": source.room_count,
        "candidate_room_count": source.candidate_room_count,
        "change_count": sum(room["action"] == "update" for room in room_reports),
        "unchanged_count": sum(room["action"] == "unchanged" for room in room_reports),
        "issue_count": len(issues),
        "issues": issues,
        "rooms": room_reports,
        "can_apply": status == "ready",
        "verified": status == "already_applied",
    }
    return _MigrationAnalysis(report, expected_by_room)


def _open_identity_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _require_preference_schema(connection: sqlite3.Connection) -> None:
    tables = {
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
            " AND name IN ('room_user_preferences', ?)",
            (MIGRATION_TABLE,),
        )
    }
    required = {"room_user_preferences", MIGRATION_TABLE}
    if tables != required:
        raise LegacyRoomPreferencesMigrationError(
            "Identity preference schema is missing; start the updated server once, then retry."
        )


def _verify_expected(
    connection: sqlite3.Connection,
    user_id: str,
    expected_by_room: dict[str, RoomUserPreferencesRecord],
) -> None:
    for room_id, expected in expected_by_room.items():
        if read_room_preferences(connection, user_id, room_id) != expected:
            raise LegacyRoomPreferencesMigrationError(
                f"Room preference verification failed for {room_id}."
            )


def _verify_committed(
    identity_path: Path,
    user_id: str,
    source_fingerprint: str,
    expected_by_room: dict[str, RoomUserPreferencesRecord],
) -> None:
    connection = _open_identity_database(identity_path)
    try:
        marker = connection.execute(
            f"SELECT 1 FROM {MIGRATION_TABLE}"
            " WHERE user_id = ? AND source_fingerprint = ?",
            (user_id, source_fingerprint),
        ).fetchone()
        if marker is None:
            raise LegacyRoomPreferencesMigrationError("Preference migration marker is missing.")
        _verify_expected(connection, user_id, expected_by_room)
    finally:
        connection.close()


def _read_plan(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise LegacyRoomPreferencesMigrationError(
            "Run room migrate-room-preferences --dry-run before --apply."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LegacyRoomPreferencesMigrationError(
            "The room preference migration plan is unreadable."
        ) from error
    if not isinstance(payload, dict):
        raise LegacyRoomPreferencesMigrationError("The room preference migration plan is invalid.")
    return payload


def _verify_plan(planned: dict[str, object], current: dict[str, object]) -> None:
    if planned.get("status") != "ready":
        raise LegacyRoomPreferencesMigrationError(
            "The dry-run plan is blocked; repair its issues and run dry-run again."
        )
    for field in (
        "version",
        "user_id",
        "source_fingerprint",
        "current_target_fingerprint",
    ):
        if planned.get(field) != current.get(field):
            raise LegacyRoomPreferencesMigrationError(
                "Legacy preferences, target user, or canonical target changed after dry-run."
            )


def _backup_inputs(
    root: Path,
    source: LegacyRoomPreferencesSource,
    *,
    identity_path: Path,
    plan_path: Path,
) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_name = f"room-preferences-migration-{timestamp}-{source.fingerprint[:12]}"
    backup_dir = root / "backups" / backup_name
    backup_dir.mkdir(parents=True, exist_ok=False)
    (backup_dir / source.path.name).write_bytes(source.raw_bytes)
    (backup_dir / plan_path.name).write_bytes(plan_path.read_bytes())
    with closing(sqlite3.connect(str(identity_path))) as source_db, closing(
        sqlite3.connect(str(backup_dir / identity_path.name))
    ) as target_db:
        source_db.backup(target_db)
    return backup_dir


def _preferences_fingerprint(
    preferences: dict[str, RoomUserPreferencesRecord],
) -> str:
    payload = {
        room_id: json.loads(encode_room_preferences(value))
        for room_id, value in sorted(preferences.items())
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


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
    identity_path: Path,
    *,
    user_id: str,
    apply: bool,
) -> dict[str, object]:
    empty_fingerprint = _preferences_fingerprint({})
    return {
        "version": MIGRATION_VERSION,
        "status": "not_needed",
        "mode": "apply" if apply else "dry_run",
        "output_root": str(root),
        "user_id": user_id,
        "source_path": str(source_path),
        "identity_database_path": str(identity_path),
        "source_fingerprint": "",
        "source_file_fingerprint": "",
        "current_target_fingerprint": empty_fingerprint,
        "planned_target_fingerprint": empty_fingerprint,
        "source_room_count": 0,
        "candidate_room_count": 0,
        "change_count": 0,
        "unchanged_count": 0,
        "issue_count": 0,
        "issues": [],
        "rooms": [],
        "can_apply": False,
        "verified": True,
        "plan_path": str(root / PLAN_FILENAME),
    }


def _issue(room_id: str, field: str, code: str, message: str) -> dict[str, str]:
    return {"room_id": room_id, "field": field, "code": code, "message": message}
