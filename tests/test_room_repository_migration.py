from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote
from uuid import uuid4

from agentsassemble.cli import build_parser, run_room_command
from agentsassemble.room_attention import AttentionEvaluation
from agentsassemble.application.room_repository_factory import (
    DEFAULT_POSTGRES_DSN_ENV,
    RoomRepositorySettings,
    RoomRepositoryUnavailable,
    build_room_repository,
)
from agentsassemble.legacy.room.repository_migration import (
    RoomRepositoryTransferError,
    _TableSpec,
    _normalized_rows,
    migrate_sqlite_rooms_to_postgres,
)
from agentsassemble.room_store import RoomStore


_POSTGRES_DSN = os.environ.get("AGENTSASSEMBLE_TEST_POSTGRES_DSN", "").strip()
_PSYCOPG_AVAILABLE = importlib.util.find_spec("psycopg") is not None

if _PSYCOPG_AVAILABLE:
    import psycopg
    from psycopg import sql

    from agentsassemble.postgres_room_repository import PostgresRoomRepository
    from agentsassemble.postgres_room_schema import upgrade_postgres_room_schema


class RoomRepositoryMigrationCliTests(unittest.TestCase):
    def test_parser_defaults_postgres_migration_to_dry_run_and_named_environment(self) -> None:
        args = build_parser().parse_args(["room", "migrate-postgres"])

        self.assertFalse(args.apply)
        self.assertEqual(args.postgres_dsn_env, DEFAULT_POSTGRES_DSN_ENV)

    def test_cli_reads_dsn_from_environment_without_putting_it_on_argv(self) -> None:
        args = build_parser().parse_args(
            ["room", "migrate-postgres", "--output-root", "/tmp/source", "--json"]
        )
        result = {
            "status": "ready",
            "mode": "dry_run",
            "can_apply": True,
            "verified": False,
            "source": {"row_counts": {"rooms": 1, "room_events": 2}, "checksum": "source"},
            "target": {},
        }
        secret_dsn = "postgresql://secret-user:secret-pass@example.invalid/rooms"

        stdout = StringIO()
        with patch.dict(os.environ, {DEFAULT_POSTGRES_DSN_ENV: secret_dsn}), patch(
            "agentsassemble.legacy.room.repository_migration.migrate_sqlite_rooms_to_postgres",
            return_value=result,
        ) as migrate, patch("sys.stdout", stdout):
            exit_code = run_room_command(args)

        self.assertEqual(exit_code, 0)
        migrate.assert_called_once_with(
            Path("/tmp/source"),
            postgres_dsn=secret_dsn,
            apply=False,
        )
        self.assertNotIn("secret", stdout.getvalue())

    def test_cli_rejects_missing_dsn_without_starting_migration(self) -> None:
        args = build_parser().parse_args(["room", "migrate-postgres"])

        stderr = StringIO()
        with patch.dict(os.environ, {}, clear=True), patch(
            "agentsassemble.legacy.room.repository_migration.migrate_sqlite_rooms_to_postgres"
        ) as migrate, patch("sys.stderr", stderr):
            exit_code = run_room_command(args)

        self.assertEqual(exit_code, 2)
        migrate.assert_not_called()
        self.assertIn(DEFAULT_POSTGRES_DSN_ENV, stderr.getvalue())

    def test_missing_sqlite_source_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(RoomRepositoryTransferError, "was not found"):
                migrate_sqlite_rooms_to_postgres(
                    Path(temp_dir),
                    postgres_dsn="postgresql://not-used.invalid/rooms",
                )

    def test_checksum_rows_preserve_numeric_key_order(self) -> None:
        spec = _TableSpec("events", ("seq", "value"), ("seq",))

        rows = _normalized_rows(
            [{"seq": 10, "value": "ten"}, {"seq": 2, "value": "two"}],
            spec,
        )

        self.assertEqual([row["seq"] for row in rows], [2, 10])

    def test_database_error_does_not_expose_dsn(self) -> None:
        class DriverFailure(RuntimeError):
            pass

        class BrokenDriver:
            @staticmethod
            def connect(*_args, **_kwargs):
                raise DriverFailure("postgresql://secret-user:secret-pass@example.invalid/rooms")

        secret_dsn = "postgresql://secret-user:secret-pass@example.invalid/rooms"
        with tempfile.TemporaryDirectory() as temp_dir:
            _build_source(Path(temp_dir))
            with patch(
                "agentsassemble.legacy.room.repository_migration._postgres_driver",
                return_value=(BrokenDriver, object(), object()),
            ), self.assertRaises(RoomRepositoryTransferError) as raised:
                migrate_sqlite_rooms_to_postgres(
                    Path(temp_dir),
                    postgres_dsn=secret_dsn,
                )

        self.assertNotIn("secret-user", str(raised.exception))
        self.assertNotIn("secret-pass", str(raised.exception))
        self.assertIn("DriverFailure", str(raised.exception))


@unittest.skipUnless(
    _PSYCOPG_AVAILABLE and _POSTGRES_DSN,
    "AGENTSASSEMBLE_TEST_POSTGRES_DSN and the postgres extra are required",
)
class PostgresRoomRepositoryMigrationTests(unittest.TestCase):
    def test_dry_run_does_not_create_postgres_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, self._postgres_schema() as test_dsn:
            _build_source(Path(temp_dir))

            report = migrate_sqlite_rooms_to_postgres(
                Path(temp_dir),
                postgres_dsn=test_dsn,
                apply=False,
            )

            self.assertEqual(report["status"], "ready")
            self.assertEqual(report["target"]["schema_state"], "absent")
            with psycopg.connect(test_dsn) as connection:
                relation = connection.execute(
                    "SELECT to_regclass('rooms')"
                ).fetchone()[0]
            self.assertIsNone(relation)

    def test_apply_preserves_all_rows_checksums_and_event_sequences(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, self._postgres_schema() as test_dsn:
            source_store = _build_source(Path(temp_dir))
            source_events = source_store.read_events("general", include_hidden=True)

            report = migrate_sqlite_rooms_to_postgres(
                Path(temp_dir),
                postgres_dsn=test_dsn,
                apply=True,
            )

            self.assertEqual(report["status"], "applied")
            self.assertTrue(report["verified"])
            self.assertTrue(report["target"]["authority_active"])
            self.assertEqual(report["source"]["checksum"], report["target"]["checksum"])
            self.assertEqual(
                report["source"]["event_sequences"],
                report["target"]["event_sequences"],
            )
            repository = PostgresRoomRepository(
                test_dsn,
                output_root=Path(temp_dir),
                migrate=False,
            )
            target_events = repository.read_events("general", include_hidden=True)
            self.assertEqual(
                [(event["id"], event["seq"]) for event in target_events],
                [(event["id"], event["seq"]) for event in source_events],
            )
            self.assertEqual(repository.session("general", "agent-a")["model"], "test-model")
            self.assertTrue(repository.room_is_deleted("deleted-room"))
            self.assertEqual(len(repository.attention_jobs("general")), 1)
            with psycopg.connect(test_dsn) as connection:
                authority = connection.execute(
                    "SELECT source_backend, source_checksum FROM room_repository_authority"
                ).fetchone()
            self.assertEqual(authority[0], "sqlite")
            self.assertEqual(authority[1], report["source"]["checksum"])

    def test_apply_refuses_nonempty_target_instead_of_merging(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, self._postgres_schema(
            migrate=True
        ) as test_dsn:
            _build_source(Path(temp_dir))
            target = PostgresRoomRepository(
                test_dsn,
                output_root=Path(temp_dir),
                migrate=False,
            )
            target.create_room("already-there")

            with self.assertRaisesRegex(RoomRepositoryTransferError, "not empty"):
                migrate_sqlite_rooms_to_postgres(
                    Path(temp_dir),
                    postgres_dsn=test_dsn,
                    apply=True,
                )

            self.assertTrue(target.room("already-there"))
            self.assertFalse(target.room("general"))

    def test_apply_refuses_partial_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, self._postgres_schema() as test_dsn:
            _build_source(Path(temp_dir))
            with psycopg.connect(test_dsn) as connection:
                connection.execute("CREATE TABLE rooms (room_id TEXT PRIMARY KEY)")

            report = migrate_sqlite_rooms_to_postgres(
                Path(temp_dir),
                postgres_dsn=test_dsn,
                apply=False,
            )
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["target"]["schema_state"], "partial")

            with self.assertRaisesRegex(RoomRepositoryTransferError, "partial"):
                migrate_sqlite_rooms_to_postgres(
                    Path(temp_dir),
                    postgres_dsn=test_dsn,
                    apply=True,
                )

    def test_schema_without_migration_authority_cannot_start_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, self._postgres_schema(
            migrate=True
        ) as test_dsn:
            with self.assertRaisesRegex(RoomRepositoryUnavailable, "not activated"):
                build_room_repository(
                    Path(temp_dir),
                    RoomRepositorySettings(
                        backend="postgresql",
                        postgres_dsn=test_dsn,
                    ),
                )

    @contextmanager
    def _postgres_schema(self, *, migrate: bool = False):
        schema_name = f"agentsassemble_transfer_{uuid4().hex[:12]}"
        with psycopg.connect(_POSTGRES_DSN, autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name))
            )
        test_dsn = _dsn_with_search_path(_POSTGRES_DSN, schema_name)
        try:
            if migrate:
                upgrade_postgres_room_schema(test_dsn)
            yield test_dsn
        finally:
            with psycopg.connect(_POSTGRES_DSN, autocommit=True) as connection:
                connection.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
                )


def _build_source(output_root: Path) -> RoomStore:
    store = RoomStore(output_root)
    store.create_room("general", label="General")
    store.upsert_participant(
        "general",
        {
            "participant_id": "agent-a",
            "display_name": "Agent A",
            "participant_type": "agent",
            "status": "joined",
        },
    )
    store.upsert_session(
        "general",
        {
            "session_id": "agent-a",
            "participant_id": "agent-a",
            "status": "available",
            "runtime_status": "idle",
            "model": "test-model",
        },
    )
    message = store.append_event(
        "general",
        "message_final",
        actor_id="operator-local",
        actor_type="human",
        content="migration fixture",
    )
    store.record_command_result(
        "general",
        "request-1",
        {"accepted": True, "event_id": message["id"]},
        principal_id="operator-local",
        action="message.send",
        payload_hash="fixture-hash",
    )
    with store.transaction("general") as transaction:
        transaction.advance_attention_state(
            "agent-a",
            observed_seq=int(message["seq"]),
            attention_evaluated_seq=int(message["seq"]),
        )
        job = transaction.record_attention_evaluation(
            AttentionEvaluation(
                room_id="general",
                source_event_id=str(message["id"]),
                source_seq=int(message["seq"]),
                outcome="selected",
                selected_participant_id="agent-a",
                eligible_participant_ids=("agent-a",),
                reasons=("direct_mention",),
            ),
            mode="shadow",
            status="completed",
        )

    now = "2026-07-14T00:00:00+00:00"
    with closing(sqlite3.connect(store.database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """INSERT INTO attention_leases(
                   room_id, lease_id, job_id, participant_id, owner_id, status,
                   acquired_at, expires_at, released_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("general", "lease-1", job["job_id"], "agent-a", "worker-1", "released", now, now, now),
        )
        connection.execute(
            """INSERT INTO scheduled_wakeups(
                   room_id, wakeup_id, participant_id, reason, wake_at, status,
                   payload_json, created_at, updated_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("general", "wakeup-1", "agent-a", "follow_up", now, "scheduled", json.dumps({"n": 1}), now, now),
        )
        connection.execute(
            """INSERT INTO conversation_obligations(
                   room_id, obligation_id, participant_id, source_event_id, kind,
                   status, due_at, payload_json, created_at, updated_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "general",
                "obligation-1",
                "agent-a",
                message["id"],
                "reply",
                "open",
                "",
                json.dumps({"source": "fixture"}),
                now,
                now,
            ),
        )
        connection.commit()
    store.create_room("deleted-room")
    store.delete_room("deleted-room", reason="fixture")
    return store


def _dsn_with_search_path(dsn: str, schema_name: str) -> str:
    separator = "&" if "?" in dsn else "?"
    option = quote(f"-csearch_path={schema_name}", safe="")
    return f"{dsn}{separator}options={option}"


if __name__ == "__main__":
    unittest.main()
