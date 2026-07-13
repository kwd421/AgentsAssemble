from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentsassemble.postgres_room_schema import (
    POSTGRES_ROOM_AUTHORITY_ID,
    POSTGRES_ROOM_SCHEMA_REVISION,
    PostgresRoomMigrationError,
    upgrade_postgres_room_schema,
)
from agentsassemble.room_database import ROOM_DATABASE_FILENAME, ROOM_SCHEMA_VERSION


class RoomRepositoryTransferError(RuntimeError):
    """SQLite room authority could not be copied to PostgreSQL exactly."""


@dataclass(frozen=True)
class _TableSpec:
    name: str
    columns: tuple[str, ...]
    key_columns: tuple[str, ...]
    json_columns: frozenset[str] = frozenset()
    boolean_columns: frozenset[str] = frozenset()
    timestamp_columns: frozenset[str] = frozenset()
    nullable_timestamp_columns: frozenset[str] = frozenset()


_TABLES = (
    _TableSpec(
        "rooms",
        ("room_id", "label", "status", "archived", "updated_at", "data_json"),
        ("room_id",),
        json_columns=frozenset({"data_json"}),
        boolean_columns=frozenset({"archived"}),
        timestamp_columns=frozenset({"updated_at"}),
    ),
    _TableSpec(
        "participants",
        ("room_id", "participant_id", "status", "role", "data_json"),
        ("room_id", "participant_id"),
        json_columns=frozenset({"data_json"}),
    ),
    _TableSpec(
        "agent_sessions",
        ("room_id", "session_id", "participant_id", "status", "runtime_status", "data_json"),
        ("room_id", "session_id"),
        json_columns=frozenset({"data_json"}),
    ),
    _TableSpec(
        "room_events",
        (
            "room_id",
            "seq",
            "event_id",
            "event_type",
            "actor_id",
            "turn_id",
            "created_at",
            "visibility",
            "payload_json",
        ),
        ("room_id", "seq"),
        json_columns=frozenset({"payload_json"}),
        timestamp_columns=frozenset({"created_at"}),
    ),
    _TableSpec(
        "command_results",
        (
            "room_id",
            "principal_id",
            "request_id",
            "action",
            "payload_hash",
            "created_at",
            "result_json",
        ),
        ("room_id", "principal_id", "request_id"),
        json_columns=frozenset({"result_json"}),
        timestamp_columns=frozenset({"created_at"}),
    ),
    _TableSpec(
        "deleted_rooms",
        ("room_id", "deleted_at", "reason"),
        ("room_id",),
        timestamp_columns=frozenset({"deleted_at"}),
    ),
    _TableSpec(
        "agent_attention_state",
        (
            "room_id",
            "participant_id",
            "last_observed_seq",
            "last_attention_evaluated_seq",
            "last_provider_sync_seq",
            "last_spoke_seq",
            "updated_at",
        ),
        ("room_id", "participant_id"),
        timestamp_columns=frozenset({"updated_at"}),
    ),
    _TableSpec(
        "attention_jobs",
        (
            "room_id",
            "job_id",
            "source_seq",
            "source_event_id",
            "mode",
            "outcome",
            "selected_participant_id",
            "eligible_participant_ids_json",
            "reasons_json",
            "status",
            "created_at",
            "updated_at",
        ),
        ("room_id", "job_id"),
        json_columns=frozenset({"eligible_participant_ids_json", "reasons_json"}),
        timestamp_columns=frozenset({"created_at", "updated_at"}),
    ),
    _TableSpec(
        "attention_leases",
        (
            "room_id",
            "lease_id",
            "job_id",
            "participant_id",
            "owner_id",
            "status",
            "acquired_at",
            "expires_at",
            "released_at",
        ),
        ("room_id", "lease_id"),
        timestamp_columns=frozenset({"acquired_at", "expires_at", "released_at"}),
        nullable_timestamp_columns=frozenset({"released_at"}),
    ),
    _TableSpec(
        "scheduled_wakeups",
        (
            "room_id",
            "wakeup_id",
            "participant_id",
            "reason",
            "wake_at",
            "status",
            "payload_json",
            "created_at",
            "updated_at",
        ),
        ("room_id", "wakeup_id"),
        json_columns=frozenset({"payload_json"}),
        timestamp_columns=frozenset({"wake_at", "created_at", "updated_at"}),
    ),
    _TableSpec(
        "conversation_obligations",
        (
            "room_id",
            "obligation_id",
            "participant_id",
            "source_event_id",
            "kind",
            "status",
            "due_at",
            "payload_json",
            "created_at",
            "updated_at",
        ),
        ("room_id", "obligation_id"),
        json_columns=frozenset({"payload_json"}),
        timestamp_columns=frozenset({"due_at", "created_at", "updated_at"}),
        nullable_timestamp_columns=frozenset({"due_at"}),
    ),
)


def migrate_sqlite_rooms_to_postgres(
    output_root: Path,
    *,
    postgres_dsn: str,
    apply: bool = False,
) -> dict[str, object]:
    """Inspect or atomically copy canonical room data to an empty PostgreSQL schema.

    The SQLite write lock is held from snapshot through PostgreSQL verification,
    so a running GUI cannot change the source behind the migration. The command
    never deletes or edits the SQLite source.
    """

    clean_dsn = str(postgres_dsn or "").strip()
    if not clean_dsn:
        raise RoomRepositoryTransferError("PostgreSQL room migration requires a configured DSN.")
    database_path = Path(output_root) / "rooms" / ROOM_DATABASE_FILENAME
    if not database_path.is_file():
        raise RoomRepositoryTransferError("Canonical SQLite room database was not found.")

    psycopg, dict_row, jsonb_type = _postgres_driver()
    source_connection = sqlite3.connect(
        str(database_path),
        timeout=1.0,
        isolation_level=None,
    )
    source_connection.row_factory = sqlite3.Row
    try:
        try:
            source_connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            raise RoomRepositoryTransferError(
                "Canonical SQLite room database is busy; stop the GUI before migration."
            ) from error
        source = _read_sqlite_snapshot(source_connection)

        schema = _postgres_schema_state(psycopg, dict_row, clean_dsn)
        if apply and schema["state"] == "absent":
            try:
                upgrade_postgres_room_schema(clean_dsn)
            except PostgresRoomMigrationError as error:
                raise RoomRepositoryTransferError(str(error)) from error
            schema = _postgres_schema_state(psycopg, dict_row, clean_dsn)

        if schema["state"] == "ready":
            target = _read_postgres_snapshot(psycopg, dict_row, clean_dsn)
        else:
            target = _empty_snapshot()
        target_empty = not any(target["row_counts"].values())
        can_apply = (
            schema["state"] in {"absent", "ready"}
            and target_empty
            and not schema["authority_active"]
        )

        if not apply:
            return _migration_report(
                status="ready" if can_apply else "blocked",
                mode="dry_run",
                source=source,
                target=target,
                schema=schema,
                can_apply=can_apply,
                verified=False,
            )
        if schema["state"] != "ready":
            raise RoomRepositoryTransferError(
                "PostgreSQL room schema is partial or has an unexpected migration revision."
            )
        if schema["authority_active"]:
            raise RoomRepositoryTransferError(
                "PostgreSQL room authority is already activated; migration will not overwrite it."
            )
        if not target_empty:
            raise RoomRepositoryTransferError(
                "PostgreSQL room repository is not empty; migration refuses to merge authorities."
            )

        target = _write_postgres_snapshot(
            psycopg,
            dict_row,
            jsonb_type,
            clean_dsn,
            source,
        )
        schema = {**schema, "authority_active": True}
        if source["checksum"] != target["checksum"]:
            raise RoomRepositoryTransferError(
                "PostgreSQL room migration checksum mismatch; target transaction was rolled back."
            )
        if source["event_sequences"] != target["event_sequences"]:
            raise RoomRepositoryTransferError(
                "PostgreSQL room migration event sequence mismatch; target transaction was rolled back."
            )
        source_connection.rollback()
        return _migration_report(
            status="applied",
            mode="apply",
            source=source,
            target=target,
            schema=schema,
            can_apply=True,
            verified=True,
        )
    except RoomRepositoryTransferError:
        source_connection.rollback()
        raise
    except sqlite3.Error as error:
        source_connection.rollback()
        raise RoomRepositoryTransferError(
            f"Canonical SQLite room migration failed: {type(error).__name__}."
        ) from None
    except Exception as error:
        source_connection.rollback()
        sqlstate = str(getattr(error, "sqlstate", "") or "")
        suffix = f" (SQLSTATE {sqlstate})" if sqlstate else ""
        raise RoomRepositoryTransferError(
            f"PostgreSQL room migration failed: {type(error).__name__}{suffix}."
        ) from None
    finally:
        source_connection.close()


def _read_sqlite_snapshot(connection: sqlite3.Connection) -> dict[str, object]:
    row = connection.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    version = int(row["value"]) if row is not None else 0
    if version != ROOM_SCHEMA_VERSION:
        raise RoomRepositoryTransferError(
            f"Unsupported SQLite room schema version {version}; expected {ROOM_SCHEMA_VERSION}."
        )
    rows = {
        spec.name: _normalized_rows(connection.execute(_select_sql(spec)).fetchall(), spec)
        for spec in _TABLES
    }
    return _snapshot(rows)


def _postgres_schema_state(psycopg: Any, dict_row: Any, dsn: str) -> dict[str, object]:
    required_tables = tuple(spec.name for spec in _TABLES) + ("room_repository_authority",)
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        existing = {
            table
            for table in required_tables
            if connection.execute("SELECT to_regclass(%s) AS relation", (table,)).fetchone()[
                "relation"
            ]
            is not None
        }
        revision = ""
        alembic_exists = connection.execute(
            "SELECT to_regclass('alembic_version') AS relation"
        ).fetchone()["relation"]
        if alembic_exists is not None:
            revision_row = connection.execute(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ).fetchone()
            revision = str((revision_row or {}).get("version_num") or "")
        authority_active = False
        if "room_repository_authority" in existing:
            authority_active = connection.execute(
                "SELECT 1 AS active FROM room_repository_authority WHERE authority_id = %s",
                (POSTGRES_ROOM_AUTHORITY_ID,),
            ).fetchone() is not None
    if not existing and not revision:
        state = "absent"
    elif len(existing) == len(required_tables) and revision == POSTGRES_ROOM_SCHEMA_REVISION:
        state = "ready"
    else:
        state = "partial"
    return {
        "state": state,
        "revision": revision,
        "missing_tables": [table for table in required_tables if table not in existing],
        "authority_active": authority_active,
    }


def _read_postgres_snapshot(psycopg: Any, dict_row: Any, dsn: str) -> dict[str, object]:
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        rows = {
            spec.name: _normalized_rows(connection.execute(_select_sql(spec)).fetchall(), spec)
            for spec in _TABLES
        }
    return _snapshot(rows)


def _write_postgres_snapshot(
    psycopg: Any,
    dict_row: Any,
    jsonb_type: Any,
    dsn: str,
    source: dict[str, object],
) -> dict[str, object]:
    source_rows = source["rows"]
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        for spec in _TABLES:
            rows = source_rows[spec.name]
            if not rows:
                continue
            placeholders = ", ".join(["%s"] * len(spec.columns))
            insert_sql = (
                f"INSERT INTO {spec.name} ({', '.join(spec.columns)}) "
                f"VALUES ({placeholders})"
            )
            values = [
                tuple(
                    _postgres_value(spec, column, row[column], jsonb_type)
                    for column in spec.columns
                )
                for row in rows
            ]
            with connection.cursor() as cursor:
                cursor.executemany(insert_sql, values)
        target_rows = {
            spec.name: _normalized_rows(connection.execute(_select_sql(spec)).fetchall(), spec)
            for spec in _TABLES
        }
        target = _snapshot(target_rows)
        if source["checksum"] != target["checksum"]:
            raise RoomRepositoryTransferError(
                "PostgreSQL room migration checksum mismatch; target transaction was rolled back."
            )
        if source["event_sequences"] != target["event_sequences"]:
            raise RoomRepositoryTransferError(
                "PostgreSQL room migration event sequence mismatch; target transaction was rolled back."
            )
        connection.execute(
            """INSERT INTO room_repository_authority(
                   authority_id, activated_at, source_backend, source_checksum
               ) VALUES(%s, %s, %s, %s)""",
            (
                POSTGRES_ROOM_AUTHORITY_ID,
                datetime.now(UTC),
                "sqlite",
                source["checksum"],
            ),
        )
    return target


def _normalized_rows(rows: list[Any], spec: _TableSpec) -> list[dict[str, object]]:
    normalized = [
        {
            column: _normalize_value(spec, column, row[column])
            for column in spec.columns
        }
        for row in rows
    ]
    normalized.sort(
        key=lambda row: tuple(_canonical_sort_value(row[column]) for column in spec.key_columns)
    )
    return normalized


def _canonical_sort_value(value: object) -> tuple[int, object]:
    if isinstance(value, bool):
        return (0, int(value))
    if isinstance(value, (int, float)):
        return (0, value)
    if value is None:
        return (2, "")
    return (1, str(value))


def _normalize_value(spec: _TableSpec, column: str, value: object) -> object:
    if column in spec.json_columns:
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(str(value or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise RoomRepositoryTransferError(
                f"Invalid JSON in canonical room table {spec.name}.{column}."
            ) from error
    if column in spec.boolean_columns:
        return bool(value)
    if column in spec.timestamp_columns:
        if value in {None, ""} and column in spec.nullable_timestamp_columns:
            return None
        return _normalized_timestamp(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    return "" if value is None else str(value)


def _normalized_timestamp(value: object) -> str:
    if isinstance(value, datetime):
        timestamp = value
    else:
        raw = str(value or "").strip().replace("Z", "+00:00")
        if not raw:
            raise RoomRepositoryTransferError("Canonical room timestamp is empty.")
        try:
            timestamp = datetime.fromisoformat(raw)
        except ValueError as error:
            raise RoomRepositoryTransferError("Canonical room timestamp is invalid.") from error
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC).isoformat(timespec="microseconds")


def _postgres_value(
    spec: _TableSpec,
    column: str,
    value: object,
    jsonb_type: Any,
) -> object:
    if column in spec.json_columns:
        return jsonb_type(value)
    if column in spec.timestamp_columns:
        if value is None:
            return None
        return datetime.fromisoformat(str(value))
    return value


def _snapshot(rows: dict[str, list[dict[str, object]]]) -> dict[str, object]:
    row_counts = {spec.name: len(rows.get(spec.name, [])) for spec in _TABLES}
    table_checksums = {
        spec.name: _checksum(rows.get(spec.name, []))
        for spec in _TABLES
    }
    event_sequences: dict[str, dict[str, int]] = {}
    for event in rows.get("room_events", []):
        room_id = str(event["room_id"])
        seq = int(event["seq"])
        summary = event_sequences.setdefault(
            room_id,
            {"count": 0, "min_seq": seq, "max_seq": seq},
        )
        summary["count"] += 1
        summary["min_seq"] = min(summary["min_seq"], seq)
        summary["max_seq"] = max(summary["max_seq"], seq)
    return {
        "rows": rows,
        "row_counts": row_counts,
        "table_checksums": table_checksums,
        "checksum": _checksum(table_checksums),
        "event_sequences": event_sequences,
    }


def _empty_snapshot() -> dict[str, object]:
    return _snapshot({spec.name: [] for spec in _TABLES})


def _migration_report(
    *,
    status: str,
    mode: str,
    source: dict[str, object],
    target: dict[str, object],
    schema: dict[str, object],
    can_apply: bool,
    verified: bool,
) -> dict[str, object]:
    return {
        "status": status,
        "mode": mode,
        "can_apply": can_apply,
        "verified": verified,
        "source": _public_snapshot(source),
        "target": {
            **_public_snapshot(target),
            "schema_state": schema["state"],
            "schema_revision": schema["revision"],
            "missing_tables": schema["missing_tables"],
            "authority_active": schema["authority_active"],
        },
    }


def _public_snapshot(snapshot: dict[str, object]) -> dict[str, object]:
    return {
        "row_counts": snapshot["row_counts"],
        "table_checksums": snapshot["table_checksums"],
        "checksum": snapshot["checksum"],
        "event_sequences": snapshot["event_sequences"],
    }


def _select_sql(spec: _TableSpec) -> str:
    order = ", ".join(spec.key_columns)
    return f"SELECT {', '.join(spec.columns)} FROM {spec.name} ORDER BY {order}"


def _checksum(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _postgres_driver() -> tuple[Any, Any, Any]:
    try:
        import psycopg
        from psycopg.rows import dict_row
        from psycopg.types.json import Jsonb
    except ModuleNotFoundError as error:
        raise RoomRepositoryTransferError(
            "PostgreSQL room migration requires the optional 'postgres' dependencies."
        ) from error
    return psycopg, dict_row, Jsonb
