from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from agentsassemble.room_global_settings import default_room_global_settings
from agentsassemble.room.text import clean_room_text
from agentsassemble.room.visibility import LEGACY_HIDDEN, VISIBLE

try:
    import fcntl
except ImportError:  # pragma: no cover - AgentsAssemble's supported hosts are Unix-like
    fcntl = None  # type: ignore[assignment]


ROOM_DATABASE_FILENAME = "rooms.sqlite3"
ROOM_SCHEMA_VERSION = 5
LEGACY_AUTHORITY_FILES = (
    "room.json",
    "participants.json",
    "sessions.json",
    "events.jsonl",
    "commands.json",
)
LEGACY_AUDIT_FILES = (*LEGACY_AUTHORITY_FILES, "events.pre-unification.jsonl")
ATTENTION_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS agent_attention_state (
           room_id TEXT NOT NULL,
           participant_id TEXT NOT NULL,
           last_observed_seq INTEGER NOT NULL DEFAULT 0,
           last_attention_evaluated_seq INTEGER NOT NULL DEFAULT 0,
           last_provider_sync_seq INTEGER NOT NULL DEFAULT 0,
           last_spoke_seq INTEGER NOT NULL DEFAULT 0,
           updated_at TEXT NOT NULL,
           PRIMARY KEY (room_id, participant_id),
           FOREIGN KEY (room_id, participant_id)
               REFERENCES participants(room_id, participant_id) ON DELETE CASCADE
       )""",
    """CREATE TABLE IF NOT EXISTS attention_jobs (
           room_id TEXT NOT NULL,
           job_id TEXT NOT NULL,
           source_seq INTEGER NOT NULL,
           source_event_id TEXT NOT NULL,
           mode TEXT NOT NULL,
           outcome TEXT NOT NULL,
           selected_participant_id TEXT NOT NULL DEFAULT '',
           eligible_participant_ids_json TEXT NOT NULL DEFAULT '[]',
           reasons_json TEXT NOT NULL DEFAULT '[]',
           status TEXT NOT NULL,
           created_at TEXT NOT NULL,
           updated_at TEXT NOT NULL,
           PRIMARY KEY (room_id, job_id),
           UNIQUE (room_id, source_seq, mode),
           FOREIGN KEY (room_id) REFERENCES rooms(room_id) ON DELETE CASCADE
       )""",
    """CREATE TABLE IF NOT EXISTS attention_leases (
           room_id TEXT NOT NULL,
           lease_id TEXT NOT NULL,
           job_id TEXT NOT NULL,
           participant_id TEXT NOT NULL,
           owner_id TEXT NOT NULL DEFAULT '',
           status TEXT NOT NULL,
           acquired_at TEXT NOT NULL,
           expires_at TEXT NOT NULL,
           released_at TEXT NOT NULL DEFAULT '',
           PRIMARY KEY (room_id, lease_id),
           FOREIGN KEY (room_id, job_id)
               REFERENCES attention_jobs(room_id, job_id) ON DELETE CASCADE
       )""",
    """CREATE TABLE IF NOT EXISTS scheduled_wakeups (
           room_id TEXT NOT NULL,
           wakeup_id TEXT NOT NULL,
           participant_id TEXT NOT NULL,
           reason TEXT NOT NULL,
           wake_at TEXT NOT NULL,
           status TEXT NOT NULL,
           payload_json TEXT NOT NULL DEFAULT '{}',
           created_at TEXT NOT NULL,
           updated_at TEXT NOT NULL,
           PRIMARY KEY (room_id, wakeup_id),
           FOREIGN KEY (room_id) REFERENCES rooms(room_id) ON DELETE CASCADE
       )""",
    """CREATE TABLE IF NOT EXISTS conversation_obligations (
           room_id TEXT NOT NULL,
           obligation_id TEXT NOT NULL,
           participant_id TEXT NOT NULL,
           source_event_id TEXT NOT NULL DEFAULT '',
           kind TEXT NOT NULL,
           status TEXT NOT NULL,
           due_at TEXT NOT NULL DEFAULT '',
           payload_json TEXT NOT NULL DEFAULT '{}',
           created_at TEXT NOT NULL,
           updated_at TEXT NOT NULL,
           PRIMARY KEY (room_id, obligation_id),
           FOREIGN KEY (room_id) REFERENCES rooms(room_id) ON DELETE CASCADE
       )""",
    "CREATE INDEX IF NOT EXISTS idx_attention_jobs_status ON attention_jobs(room_id, status, source_seq)",
    "CREATE INDEX IF NOT EXISTS idx_attention_leases_expiry ON attention_leases(status, expires_at)",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_attention_active_lease
       ON attention_leases(room_id, job_id) WHERE status = 'active'""",
    "CREATE INDEX IF NOT EXISTS idx_scheduled_wakeups_due ON scheduled_wakeups(status, wake_at)",
    """CREATE INDEX IF NOT EXISTS idx_conversation_obligations_open
       ON conversation_obligations(room_id, participant_id, status)""",
)


class RoomDatabaseMigrationError(RuntimeError):
    """Legacy room state could not be moved without losing or reordering data."""


def open_room_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        str(path),
        timeout=10.0,
        isolation_level=None,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_room_database(rooms_root: Path, database_path: Path) -> dict[str, object]:
    """Create the room database and atomically import legacy JSON authority once."""

    rooms_root.mkdir(parents=True, exist_ok=True)
    with _migration_lock(rooms_root):
        if database_path.exists():
            connection = open_room_database(database_path)
            try:
                if _schema_version(connection) is not None:
                    _migrate_schema(connection)
                _create_schema(connection)
                _scrub_legacy_source_paths(connection)
                _validate_schema_version(connection)
                connection.execute("PRAGMA journal_mode = WAL")
                return _read_migration_report(connection)
            finally:
                connection.close()

        legacy_rooms = _discover_legacy_rooms(rooms_root)
        if not legacy_rooms:
            connection = open_room_database(database_path)
            try:
                _create_schema(connection)
                report = _empty_migration_report()
                _write_schema_metadata(connection, report)
                connection.execute("PRAGMA journal_mode = WAL")
                return report
            finally:
                connection.close()

        migration_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup_root = rooms_root / "_migration_backup" / migration_id
        temporary_path = database_path.with_name(f".{database_path.name}.{uuid4().hex}.migrating")
        report: dict[str, object]
        try:
            connection = open_room_database(temporary_path)
            try:
                _create_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                try:
                    report = _import_legacy_rooms(connection, legacy_rooms)
                    report["backup_path"] = str(backup_root)
                    report["migration_id"] = migration_id
                    _write_schema_metadata(connection, report)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                _validate_import(connection, report)
            finally:
                connection.close()

            archived_paths = _copy_legacy_files(legacy_rooms, backup_root)
            os.replace(temporary_path, database_path)
            connection = open_room_database(database_path)
            try:
                connection.execute("PRAGMA journal_mode = WAL")
            finally:
                connection.close()
            for path in archived_paths:
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue
            return report
        except RoomDatabaseMigrationError:
            temporary_path.unlink(missing_ok=True)
            raise
        except Exception as error:
            temporary_path.unlink(missing_ok=True)
            raise RoomDatabaseMigrationError(f"Room database migration failed: {error}") from error


def migration_report(connection: sqlite3.Connection) -> dict[str, object]:
    return _read_migration_report(connection)


def canonical_event_from_record(
    record: dict[str, object],
    room_id: str,
    sequence: int,
) -> dict[str, object]:
    if record.get("id") and record.get("type"):
        event = dict(record)
        event["v"] = 1
        event["seq"] = _safe_int(record.get("seq")) or sequence
        event["room_id"] = clean_room_text(record.get("room_id"), limit=128) or room_id
        actor = record.get("actor") if isinstance(record.get("actor"), dict) else {}
        participant_id = clean_room_text(
            actor.get("participant_id") or record.get("participant_id") or record.get("actor_id"),
            limit=128,
        )
        participant_type = _participant_type(
            actor.get("participant_type") or record.get("participant_type") or record.get("actor_type"),
            participant_id=participant_id,
            inferred_agent=bool(record.get("participant_id")),
        )
        event["actor"] = {
            "participant_id": participant_id,
            "participant_type": participant_type,
        }
        return event

    legacy_id = clean_room_text(record.get("event_id"), limit=128)
    legacy_kind = clean_room_text(record.get("kind"), limit=64)
    if not legacy_id or not legacy_kind:
        return {}
    event_type = {
        "user_message": "message_final",
        "agent_message": "message_final",
        "agent_delta": "message_delta",
        "agent_error": "error",
        "agent_input": "agent_input",
        "system": "system",
    }.get(legacy_kind, legacy_kind)
    participant_id = clean_room_text(record.get("actor_id"), limit=128)
    participant_type = _participant_type(
        record.get("actor_type"),
        participant_id=participant_id,
        inferred_agent=legacy_kind.startswith("agent_"),
    )
    metadata = dict(record.get("metadata")) if isinstance(record.get("metadata"), dict) else {}
    event: dict[str, object] = {
        "v": 1,
        "id": legacy_id,
        "seq": sequence,
        "created_at": clean_room_text(record.get("created_at"), limit=128) or _now(),
        "room_id": clean_room_text(record.get("room_id"), limit=128) or room_id,
        "type": event_type,
        "actor": {
            "participant_id": participant_id,
            "participant_type": participant_type,
        },
        "actor_id": participant_id,
        "actor_type": participant_type,
        "content": clean_room_text(record.get("content"), limit=12000),
    }
    if metadata:
        event["metadata"] = metadata
        source_event_id = clean_room_text(metadata.get("source_event_id"), limit=128)
        if source_event_id:
            event["source_event_id"] = source_event_id
    return {key: value for key, value in event.items() if value not in (None, "", [], {})}


def event_visibility(event: dict[str, object]) -> str:
    explicit = clean_room_text(event.get("visibility"), limit=32)
    if explicit in {VISIBLE, LEGACY_HIDDEN}:
        return explicit
    if str(event.get("type") or "") != "message_final":
        return VISIBLE
    actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
    if str(actor.get("participant_type") or "") != "agent":
        return VISIBLE

    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    source = str(event.get("message_source") or metadata.get("message_source") or "").casefold()
    if source and source not in {"terminal_capture", "pty", "pty_terminal", "terminal"}:
        return VISIBLE
    content = str(event.get("content") or "")
    if not content:
        return VISIBLE
    markers = _terminal_chrome_marker_count(content)
    if source and markers >= 2:
        return LEGACY_HIDDEN
    if not source and markers >= 3:
        return LEGACY_HIDDEN
    return VISIBLE


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rooms (
            room_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            status TEXT NOT NULL,
            archived INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            data_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS room_settings (
            room_id TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            data_json TEXT NOT NULL,
            FOREIGN KEY (room_id) REFERENCES rooms(room_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS participants (
            room_id TEXT NOT NULL,
            participant_id TEXT NOT NULL,
            status TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT '',
            data_json TEXT NOT NULL,
            PRIMARY KEY (room_id, participant_id)
        );
        CREATE TABLE IF NOT EXISTS agent_sessions (
            room_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            participant_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            runtime_status TEXT NOT NULL DEFAULT '',
            data_json TEXT NOT NULL,
            PRIMARY KEY (room_id, session_id)
        );
        CREATE TABLE IF NOT EXISTS room_events (
            room_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            event_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor_id TEXT NOT NULL DEFAULT '',
            turn_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            visibility TEXT NOT NULL DEFAULT 'visible',
            payload_json TEXT NOT NULL,
            PRIMARY KEY (room_id, seq),
            UNIQUE (room_id, event_id)
        );
        CREATE TABLE IF NOT EXISTS command_results (
            room_id TEXT NOT NULL,
            principal_id TEXT NOT NULL DEFAULT '',
            request_id TEXT NOT NULL,
            action TEXT NOT NULL DEFAULT '',
            payload_hash TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            result_json TEXT NOT NULL,
            PRIMARY KEY (room_id, principal_id, request_id)
        );
        CREATE TABLE IF NOT EXISTS deleted_rooms (
            room_id TEXT PRIMARY KEY,
            deleted_at TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            principal_id TEXT NOT NULL DEFAULT '',
            request_id TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL DEFAULT '',
            payload_hash TEXT NOT NULL DEFAULT '',
            cleanup_status TEXT NOT NULL DEFAULT 'complete',
            room_name TEXT NOT NULL DEFAULT '',
            result_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_rooms_updated ON rooms(updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_participants_status ON participants(room_id, status);
        CREATE INDEX IF NOT EXISTS idx_sessions_participant ON agent_sessions(room_id, participant_id);
        CREATE INDEX IF NOT EXISTS idx_events_type_seq ON room_events(room_id, event_type, seq);
        CREATE INDEX IF NOT EXISTS idx_events_visibility_seq ON room_events(room_id, visibility, seq);
        CREATE INDEX IF NOT EXISTS idx_commands_created ON command_results(room_id, created_at DESC);
        """
    )
    _create_attention_schema(connection)


def _validate_schema_version(connection: sqlite3.Connection) -> None:
    row = connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        _write_schema_metadata(connection, _empty_migration_report())
        return
    version = _safe_int(row["value"])
    if version != ROOM_SCHEMA_VERSION:
        raise RoomDatabaseMigrationError(
            f"Unsupported room database schema version {version}; expected {ROOM_SCHEMA_VERSION}."
        )


def _migrate_schema(connection: sqlite3.Connection) -> None:
    version = _schema_version(connection)
    if version is None or version == ROOM_SCHEMA_VERSION:
        return
    if version > ROOM_SCHEMA_VERSION:
        raise RoomDatabaseMigrationError(
            f"Unsupported room database schema version {version}; expected {ROOM_SCHEMA_VERSION}."
        )
    if version not in {1, 2, 3, 4}:
        raise RoomDatabaseMigrationError(f"Unsupported room database schema version {version}.")
    connection.execute("BEGIN IMMEDIATE")
    try:
        if version == 1:
            connection.execute("ALTER TABLE command_results RENAME TO command_results_v1")
            connection.execute(
                """CREATE TABLE command_results (
                       room_id TEXT NOT NULL,
                       principal_id TEXT NOT NULL DEFAULT '',
                       request_id TEXT NOT NULL,
                       action TEXT NOT NULL DEFAULT '',
                       payload_hash TEXT NOT NULL DEFAULT '',
                       created_at TEXT NOT NULL,
                       result_json TEXT NOT NULL,
                       PRIMARY KEY (room_id, principal_id, request_id)
                   )"""
            )
            connection.execute(
                """INSERT INTO command_results(
                       room_id, principal_id, request_id, action, payload_hash, created_at, result_json
                   ) SELECT room_id, '', request_id, '', '', created_at, result_json FROM command_results_v1"""
            )
            connection.execute("DROP TABLE command_results_v1")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_commands_created ON command_results(room_id, created_at DESC)"
            )
            version = 2
        if version == 2:
            _create_attention_schema(connection)
            version = 3
        if version == 3:
            existing_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(deleted_rooms)").fetchall()
            }
            for column, declaration in (
                ("principal_id", "TEXT NOT NULL DEFAULT ''"),
                ("request_id", "TEXT NOT NULL DEFAULT ''"),
                ("action", "TEXT NOT NULL DEFAULT ''"),
                ("payload_hash", "TEXT NOT NULL DEFAULT ''"),
                ("cleanup_status", "TEXT NOT NULL DEFAULT 'complete'"),
                ("room_name", "TEXT NOT NULL DEFAULT ''"),
                ("result_json", "TEXT NOT NULL DEFAULT '{}'"),
            ):
                if column not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE deleted_rooms ADD COLUMN {column} {declaration}"
                    )
            version = 4
        if version == 4:
            _create_room_settings_schema(connection)
            _backfill_room_settings(connection)
            version = 5
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
            (str(version),),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _create_attention_schema(connection: sqlite3.Connection) -> None:
    for statement in ATTENTION_SCHEMA_STATEMENTS:
        connection.execute(statement)


def _create_room_settings_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS room_settings (
               room_id TEXT PRIMARY KEY,
               updated_at TEXT NOT NULL,
               data_json TEXT NOT NULL,
               FOREIGN KEY (room_id) REFERENCES rooms(room_id) ON DELETE CASCADE
           )"""
    )


def _backfill_room_settings(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """SELECT rooms.room_id, rooms.label, rooms.updated_at
           FROM rooms
           LEFT JOIN room_settings ON room_settings.room_id = rooms.room_id
           WHERE room_settings.room_id IS NULL"""
    ).fetchall()
    for row in rows:
        connection.execute(
            "INSERT INTO room_settings(room_id, updated_at, data_json) VALUES(?, ?, ?)",
            (
                str(row["room_id"]),
                str(row["updated_at"]),
                _json_dumps(default_room_global_settings(label=str(row["label"] or ""))),
            ),
        )


def _schema_version(connection: sqlite3.Connection) -> int | None:
    try:
        row = connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.OperationalError:
        return None
    return _safe_int(row["value"]) if row is not None else None


def _scrub_legacy_source_paths(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT room_id, seq, payload_json FROM room_events WHERE payload_json LIKE '%legacy_source_path%'"
    ).fetchall()
    if not rows:
        return
    connection.execute("BEGIN IMMEDIATE")
    try:
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict) or "legacy_source_path" not in payload:
                continue
            payload.pop("legacy_source_path", None)
            connection.execute(
                "UPDATE room_events SET payload_json = ? WHERE room_id = ? AND seq = ?",
                (_json_dumps(payload), row["room_id"], row["seq"]),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _write_schema_metadata(connection: sqlite3.Connection, report: dict[str, object]) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
        (str(ROOM_SCHEMA_VERSION),),
    )
    connection.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('legacy_migration_report', ?)",
        (_json_dumps(report),),
    )


def _read_migration_report(connection: sqlite3.Connection) -> dict[str, object]:
    row = connection.execute("SELECT value FROM schema_meta WHERE key = 'legacy_migration_report'").fetchone()
    if row is None:
        return _empty_migration_report()
    try:
        report = json.loads(str(row["value"]))
    except (json.JSONDecodeError, ValueError):
        return _empty_migration_report()
    return dict(report) if isinstance(report, dict) else _empty_migration_report()


def _empty_migration_report() -> dict[str, object]:
    return {
        "migrated": False,
        "room_count": 0,
        "participant_count": 0,
        "session_count": 0,
        "event_count": 0,
        "hidden_event_count": 0,
        "command_count": 0,
        "backup_path": "",
        "migration_id": "",
        "rooms": {},
    }


def _discover_legacy_rooms(rooms_root: Path) -> dict[str, Path]:
    discovered: dict[str, Path] = {}
    for room_dir in rooms_root.iterdir():
        if not room_dir.is_dir() or room_dir.name.startswith("_") or room_dir.name.startswith("."):
            continue
        if not _safe_room_name(room_dir.name):
            continue
        if any((room_dir / filename).is_file() for filename in LEGACY_AUTHORITY_FILES):
            discovered[room_dir.name] = room_dir
    return discovered


def _import_legacy_rooms(
    connection: sqlite3.Connection,
    legacy_rooms: dict[str, Path],
) -> dict[str, object]:
    report = _empty_migration_report()
    report["migrated"] = True
    room_reports: dict[str, object] = {}
    for room_id, room_dir in sorted(legacy_rooms.items()):
        room = _read_json_object_strict(room_dir / "room.json", optional=True)
        now = _now()
        room = {
            **room,
            "room_id": room_id,
            "label": clean_room_text(room.get("label"), limit=128) or room_id,
            "status": clean_room_text(room.get("status"), limit=32) or "active",
            "created_at": clean_room_text(room.get("created_at"), limit=128) or now,
            "updated_at": clean_room_text(room.get("updated_at"), limit=128) or now,
        }
        connection.execute(
            "INSERT INTO rooms(room_id, label, status, archived, updated_at, data_json) VALUES(?, ?, ?, ?, ?, ?)",
            (
                room_id,
                str(room["label"]),
                str(room["status"]),
                1 if room["status"] == "archived" else 0,
                str(room["updated_at"]),
                _json_dumps(room),
            ),
        )
        connection.execute(
            "INSERT INTO room_settings(room_id, updated_at, data_json) VALUES(?, ?, ?)",
            (
                room_id,
                str(room["updated_at"]),
                _json_dumps(default_room_global_settings(label=str(room["label"]))),
            ),
        )

        participants = _read_json_list_strict(room_dir / "participants.json", "participants")
        seen_participants: set[str] = set()
        for participant in participants:
            participant_id = clean_room_text(
                participant.get("participant_id") or participant.get("agent_id"), limit=128
            )
            if not participant_id or participant_id in seen_participants:
                raise RoomDatabaseMigrationError(f"Invalid or duplicate participant in room {room_id}.")
            seen_participants.add(participant_id)
            participant = {**participant, "room_id": room_id, "participant_id": participant_id}
            connection.execute(
                "INSERT INTO participants(room_id, participant_id, status, role, data_json) VALUES(?, ?, ?, ?, ?)",
                (
                    room_id,
                    participant_id,
                    str(participant.get("status") or "joined"),
                    str(participant.get("role") or ""),
                    _json_dumps(participant),
                ),
            )

        sessions = _read_json_list_strict(room_dir / "sessions.json", "sessions")
        seen_sessions: set[str] = set()
        for session in sessions:
            session_id = clean_room_text(session.get("session_id"), limit=128)
            if not session_id or session_id in seen_sessions:
                raise RoomDatabaseMigrationError(f"Invalid or duplicate Agent Session in room {room_id}.")
            seen_sessions.add(session_id)
            session = {**session, "room_id": room_id, "session_id": session_id}
            connection.execute(
                """INSERT INTO agent_sessions(
                       room_id, session_id, participant_id, status, runtime_status, data_json
                   ) VALUES(?, ?, ?, ?, ?, ?)""",
                (
                    room_id,
                    session_id,
                    str(session.get("participant_id") or ""),
                    str(session.get("status") or "attached"),
                    str(session.get("runtime_status") or ""),
                    _json_dumps(session),
                ),
            )

        event_records = _read_jsonl_strict(room_dir / "events.jsonl")
        seen_event_ids: set[str] = set()
        seen_sequences: set[int] = set()
        previous_sequence = 0
        hidden_count = 0
        for index, record in enumerate(event_records, start=1):
            event = canonical_event_from_record(record, room_id, index)
            if not event:
                raise RoomDatabaseMigrationError(f"Unrecognized event record {index} in room {room_id}.")
            event_id = clean_room_text(event.get("id"), limit=128)
            sequence = _safe_int(event.get("seq"))
            if not event_id or event_id in seen_event_ids:
                raise RoomDatabaseMigrationError(f"Invalid or duplicate event id in room {room_id}.")
            if sequence <= previous_sequence or sequence in seen_sequences:
                raise RoomDatabaseMigrationError(f"Event sequence is not strictly increasing in room {room_id}.")
            seen_event_ids.add(event_id)
            seen_sequences.add(sequence)
            previous_sequence = sequence
            visibility = event_visibility(event)
            if visibility != VISIBLE:
                event["visibility"] = visibility
                hidden_count += 1
            actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
            connection.execute(
                """INSERT INTO room_events(
                       room_id, seq, event_id, event_type, actor_id, turn_id,
                       created_at, visibility, payload_json
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    room_id,
                    sequence,
                    event_id,
                    str(event.get("type") or "message"),
                    str(actor.get("participant_id") or ""),
                    str(event.get("turn_id") or ""),
                    str(event.get("created_at") or now),
                    visibility,
                    _json_dumps(event),
                ),
            )

        commands = _read_json_list_strict(room_dir / "commands.json", "commands")
        command_count = 0
        for command in commands:
            request_id = clean_room_text(command.get("request_id"), limit=128)
            result = command.get("result")
            if not request_id or not isinstance(result, dict):
                continue
            cursor = connection.execute(
                """INSERT OR IGNORE INTO command_results(
                       room_id, request_id, created_at, result_json
                   ) VALUES(?, ?, ?, ?)""",
                (room_id, request_id, str(command.get("created_at") or now), _json_dumps(result)),
            )
            command_count += max(0, int(cursor.rowcount or 0))

        room_reports[room_id] = {
            "participant_count": len(participants),
            "session_count": len(sessions),
            "event_count": len(event_records),
            "hidden_event_count": hidden_count,
            "command_count": command_count,
            "max_seq": previous_sequence,
        }
        report["room_count"] = int(report["room_count"]) + 1
        report["participant_count"] = int(report["participant_count"]) + len(participants)
        report["session_count"] = int(report["session_count"]) + len(sessions)
        report["event_count"] = int(report["event_count"]) + len(event_records)
        report["hidden_event_count"] = int(report["hidden_event_count"]) + hidden_count
        report["command_count"] = int(report["command_count"]) + command_count
    report["rooms"] = room_reports
    return report


def _validate_import(connection: sqlite3.Connection, report: dict[str, object]) -> None:
    table_counts = {
        "room_count": "rooms",
        "participant_count": "participants",
        "session_count": "agent_sessions",
        "event_count": "room_events",
        "command_count": "command_results",
    }
    for report_key, table in table_counts.items():
        actual = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        expected = int(report.get(report_key) or 0)
        if actual != expected:
            raise RoomDatabaseMigrationError(
                f"Room migration validation failed for {table}: expected {expected}, found {actual}."
            )
    rooms = report.get("rooms") if isinstance(report.get("rooms"), dict) else {}
    for room_id, raw_room_report in rooms.items():
        room_report = raw_room_report if isinstance(raw_room_report, dict) else {}
        row = connection.execute(
            "SELECT COUNT(*) AS count, COALESCE(MAX(seq), 0) AS max_seq FROM room_events WHERE room_id = ?",
            (room_id,),
        ).fetchone()
        if int(row["count"]) != int(room_report.get("event_count") or 0):
            raise RoomDatabaseMigrationError(f"Room event count validation failed for {room_id}.")
        if int(row["max_seq"]) != int(room_report.get("max_seq") or 0):
            raise RoomDatabaseMigrationError(f"Room event sequence validation failed for {room_id}.")


def _copy_legacy_files(legacy_rooms: dict[str, Path], backup_root: Path) -> list[Path]:
    copied: list[Path] = []
    for room_id, room_dir in sorted(legacy_rooms.items()):
        target_dir = backup_root / room_id
        for filename in LEGACY_AUDIT_FILES:
            source = room_dir / filename
            if not source.is_file():
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target_dir / filename)
            copied.append(source)
    return copied


def _read_json_object_strict(path: Path, *, optional: bool = False) -> dict[str, object]:
    if not path.exists() and optional:
        return {}
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise RoomDatabaseMigrationError(f"Could not read legacy room file {path}: {error}") from error
    if not isinstance(payload, dict):
        raise RoomDatabaseMigrationError(f"Legacy room file {path} must contain a JSON object.")
    return dict(payload)


def _read_json_list_strict(path: Path, key: str) -> list[dict[str, object]]:
    if not path.exists():
        return []
    payload = _read_json_object_strict(path)
    items = payload.get(key)
    if items is None:
        return []
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise RoomDatabaseMigrationError(f"Legacy room file {path} has an invalid {key} list.")
    return [dict(item) for item in items]


def _read_jsonl_strict(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    records: list[dict[str, object]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise RoomDatabaseMigrationError(f"Could not read legacy event log {path}: {error}") from error
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError) as error:
            raise RoomDatabaseMigrationError(
                f"Legacy event log {path} has invalid JSON on line {line_number}."
            ) from error
        if not isinstance(record, dict):
            raise RoomDatabaseMigrationError(
                f"Legacy event log {path} line {line_number} must be a JSON object."
            )
        records.append(record)
    return records


def _terminal_chrome_marker_count(content: str) -> int:
    markers = 0
    if "\x1b[" in content:
        markers += 1
    if any(character in content for character in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
        markers += 1
    patterns = (
        r"(?im)^\s*[›>]\s*(?:type\s+)?/(?:help|model|status)\b",
        r"(?i)\besc\s+to\s+(?:interrupt|cancel)\b",
        r"(?i)\b(?:ctrl|control)[-+ ]c\b",
        r"(?im)^\s*(?:working|thinking|generating)\s*\([^\n]{0,80}\)\s*$",
        r"(?i)\b(?:tokens|context)\s+(?:left|remaining)\b",
        r"(?i)\bpress\s+\?\s+for\s+shortcuts\b",
    )
    markers += sum(1 for pattern in patterns if re.search(pattern, content))
    return markers


def _participant_type(value: object, *, participant_id: str, inferred_agent: bool) -> str:
    participant_type = clean_room_text(value, limit=32)
    if participant_type == "user":
        participant_type = "human"
    if participant_id and not participant_type:
        participant_type = "agent" if inferred_agent else "human"
    return participant_type


def _safe_room_name(value: str) -> bool:
    return bool(value and value not in {".", ".."} and "/" not in value and "\\" not in value)


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _json_dumps(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def _migration_lock(rooms_root: Path) -> Iterator[None]:
    lock_path = rooms_root / ".migration.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
