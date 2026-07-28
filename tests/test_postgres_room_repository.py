from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import quote
from uuid import uuid4

from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.application.room_repository_factory import RoomRepositorySettings, build_room_repository
from tests.room_repository_contract import RoomRepositoryContractMixin


_POSTGRES_DSN = os.environ.get("AGENTSASSEMBLE_TEST_POSTGRES_DSN", "").strip()
_PSYCOPG_AVAILABLE = importlib.util.find_spec("psycopg") is not None

if _PSYCOPG_AVAILABLE:
    import psycopg
    from psycopg import sql

    from agentsassemble.postgres_room_repository import PostgresRoomRepository
    from agentsassemble.postgres_room_schema import (
        POSTGRES_ROOM_AUTHORITY_ID,
        upgrade_postgres_room_schema,
    )
    from agentsassemble.persistence.postgres.room.queries import (
        _VOTE_BALLOT_EVENTS_QUERY,
    )


class _FakeRepositoryConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[object, object]] = []

    def transaction(self):
        return nullcontext()

    def execute(self, statement: object, parameters: object = None):
        self.executed.append((statement, parameters))
        return self


class _FakeRepositoryPool:
    def __init__(self, kwargs: dict[str, object]) -> None:
        self.kwargs = kwargs
        self.closed = False
        self.waited = False
        self.borrow_count = 0
        self.connection_value = _FakeRepositoryConnection()

    def wait(self, timeout: float) -> None:
        self.waited = timeout > 0

    def connection(self, timeout: float):
        self.borrow_count += 1
        return nullcontext(self.connection_value)

    def close(self, timeout: float) -> None:
        self.closed = True

    def get_stats(self) -> dict[str, object]:
        return {"pool_size": 1, "conninfo": "secret-password"}


def _fake_repository() -> tuple[object, _FakeRepositoryPool]:
    pools: list[_FakeRepositoryPool] = []

    def factory(**kwargs: object) -> _FakeRepositoryPool:
        pool = _FakeRepositoryPool(dict(kwargs))
        pools.append(pool)
        return pool

    repository = PostgresRoomRepository(
        "postgresql://secret-user:secret-password@example.invalid/rooms",
        pool_factory=factory,
    )
    return repository, pools[0]


@unittest.skipUnless(_PSYCOPG_AVAILABLE, "the postgres extra is required")
class PostgresRoomRepositoryPoolIntegrationTests(unittest.TestCase):
    def test_borrowed_application_database_is_not_closed_by_repository(self) -> None:
        database = MagicMock()
        repository = PostgresRoomRepository(database=database)

        repository.close()

        self.assertIs(repository._connections, database)
        database.close.assert_not_called()

    def test_repository_borrows_from_one_pool_and_exposes_safe_diagnostics(self) -> None:
        repository, pool = _fake_repository()
        try:
            with repository._connection() as connection:
                self.assertIs(connection, pool.connection_value)

            self.assertTrue(pool.waited)
            self.assertEqual(pool.borrow_count, 1)
            diagnostics = repository.public_diagnostics()
            self.assertEqual(diagnostics["backend"], "postgresql")
            self.assertNotIn("secret-user", str(diagnostics))
            self.assertNotIn("secret-password", str(diagnostics))
        finally:
            repository.close()

        self.assertTrue(pool.closed)

    def test_command_unit_reuses_transaction_connection_for_repository_reads(self) -> None:
        repository, pool = _fake_repository()
        connections: list[object] = []

        def read_command(connection, *_args, **_kwargs):
            connections.append(connection)
            return {}

        def read_room(connection, room_id):
            connections.append(connection)
            return {"room_id": room_id}

        def record_command(connection, _room_id, _request_id, result, **_kwargs):
            connections.append(connection)
            return result

        try:
            with patch(
                "agentsassemble.persistence.postgres.room.repository.read_command_record",
                side_effect=read_command,
            ), patch(
                "agentsassemble.persistence.postgres.room.repository.read_room",
                side_effect=read_room,
            ), patch(
                "agentsassemble.persistence.postgres.room.repository."
                "persist_command_result",
                side_effect=record_command,
            ):
                with RoomCommandUnitOfWork(
                    repository,
                    room_id="general",
                    principal_id="host-a",
                    request_id="request-a",
                    action="message.send",
                    payload={"content": "hello"},
                ) as unit:
                    self.assertEqual(repository.room("general"), {"room_id": "general"})
                    unit.build_ack({"status": "sent"})
                    unit.record_ack()

                self.assertEqual(repository.room("general"), {"room_id": "general"})
        finally:
            repository.close()

        self.assertEqual(pool.borrow_count, 2)
        self.assertEqual(connections[:3], [pool.connection_value] * 3)
        self.assertIs(connections[3], pool.connection_value)

    def test_nested_transaction_is_rejected_before_second_pool_checkout(self) -> None:
        repository, pool = _fake_repository()
        try:
            with repository.transaction("general"):
                with self.assertRaisesRegex(RuntimeError, "Nested PostgreSQL room transactions"):
                    with repository.transaction("general"):
                        pass
        finally:
            repository.close()

        self.assertEqual(pool.borrow_count, 1)


@unittest.skipUnless(
    _PSYCOPG_AVAILABLE and _POSTGRES_DSN,
    "AGENTSASSEMBLE_TEST_POSTGRES_DSN and the postgres extra are required",
)
class PostgresRoomRepositoryContractTests(RoomRepositoryContractMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema_name = f"agentsassemble_test_{uuid4().hex[:12]}"
        with psycopg.connect(_POSTGRES_DSN, autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema_name))
            )
        cls.test_dsn = _dsn_with_search_path(_POSTGRES_DSN, cls.schema_name)
        upgrade_postgres_room_schema(cls.test_dsn)
        with psycopg.connect(cls.test_dsn) as connection:
            connection.execute(
                """INSERT INTO room_repository_authority(
                       authority_id, activated_at, source_backend, source_checksum
                   ) VALUES(%s, NOW(), %s, %s)""",
                (POSTGRES_ROOM_AUTHORITY_ID, "test", "test-checksum"),
            )
        cls._temporary_directory = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            with psycopg.connect(_POSTGRES_DSN, autocommit=True) as connection:
                connection.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema_name))
                )
        finally:
            cls._temporary_directory.cleanup()

    def setUp(self) -> None:
        with psycopg.connect(self.test_dsn) as connection:
            connection.execute("TRUNCATE TABLE deleted_rooms, rooms CASCADE")
        self.repository = PostgresRoomRepository(
            self.test_dsn,
            output_root=Path(self._temporary_directory.name),
            migrate=False,
        )

    def tearDown(self) -> None:
        self.repository.close()

    def test_postgres_repository_implements_repository_protocol(self) -> None:
        self.assertIsInstance(self.repository, RoomRepository)

    def test_ensure_room_rejects_missing_or_invalid_settings(self) -> None:
        self.repository.create_room("missing-settings", label="Missing")
        self.repository.create_room("invalid-settings", label="Invalid")
        self.repository.create_room("invalid-room", label="Invalid room")
        with psycopg.connect(self.test_dsn) as connection:
            connection.execute(
                "DELETE FROM room_settings WHERE room_id = %s",
                ("missing-settings",),
            )
            connection.execute(
                "UPDATE room_settings SET data_json = %s::jsonb WHERE room_id = %s",
                ('{"unexpected": true}', "invalid-settings"),
            )
            connection.execute(
                "UPDATE rooms SET data_json = %s::jsonb WHERE room_id = %s",
                ("{}", "invalid-room"),
            )
            connection.execute(
                "DELETE FROM room_settings WHERE room_id = %s",
                ("invalid-room",),
            )

        with self.assertRaisesRegex(ValueError, "settings.*missing"):
            self.repository.ensure_room("missing-settings")
        with self.assertRaises(ValueError):
            self.repository.ensure_room("invalid-settings")
        with self.assertRaisesRegex(ValueError, "record is invalid"):
            self.repository.ensure_room("invalid-room")

        with psycopg.connect(self.test_dsn) as connection:
            settings_count = connection.execute(
                "SELECT COUNT(*) AS count FROM room_settings WHERE room_id = %s",
                ("invalid-room",),
            ).fetchone()[0]
        self.assertEqual(settings_count, 0)
        self.assertEqual(
            [event["type"] for event in self.repository.read_events("invalid-room")],
            ["room_created"],
        )

    def test_repository_representation_does_not_disclose_dsn(self) -> None:
        self.assertEqual(repr(self.repository), "PostgresRoomRepository(configured=True)")
        self.assertNotIn(self.test_dsn, repr(self.repository))

    def test_repository_constructor_does_not_migrate_by_default(self) -> None:
        schema_name = f"agentsassemble_unmigrated_{uuid4().hex[:12]}"
        with psycopg.connect(_POSTGRES_DSN, autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name))
            )
        repository = None
        try:
            unmigrated_dsn = _dsn_with_search_path(_POSTGRES_DSN, schema_name)
            repository = PostgresRoomRepository(unmigrated_dsn)
            repository.close()
            repository = None
            with psycopg.connect(_POSTGRES_DSN) as connection:
                table_count = connection.execute(
                    "SELECT COUNT(*) FROM pg_tables WHERE schemaname = %s",
                    (schema_name,),
                ).fetchone()[0]
            self.assertEqual(table_count, 0)
        finally:
            if repository is not None:
                repository.close()
            with psycopg.connect(_POSTGRES_DSN, autocommit=True) as connection:
                connection.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
                )

    def test_factory_builds_postgres_without_opening_sqlite(self) -> None:
        output_root = Path(self._temporary_directory.name) / "factory"
        repository = build_room_repository(
            output_root,
            RoomRepositorySettings(backend="postgresql", postgres_dsn=self.test_dsn),
        )

        self.assertIsInstance(repository, PostgresRoomRepository)
        self.assertFalse((output_root / "rooms" / "rooms.sqlite3").exists())
        repository.close()

    def test_gui_handler_uses_postgres_for_controller_and_routes_without_sqlite(self) -> None:
        from agentsassemble.gui import _make_handler

        output_root = Path(self._temporary_directory.name) / f"gui-{uuid4().hex[:8]}"
        handler = _make_handler(
            output_root,
            room_repository_override=self.repository,
        )
        try:
            self.assertIs(handler.room_repository, self.repository)
            self.assertIs(handler.gui_deps.rooms, self.repository)
            self.assertIs(handler.room_realtime_controller.store, self.repository)
            self.assertFalse((output_root / "rooms" / "rooms.sqlite3").exists())
        finally:
            handler.room_realtime_controller.close()

    def test_concurrent_event_writers_allocate_contiguous_room_sequence(self) -> None:
        self.repository.create_room("general")

        def append(index: int) -> int:
            event = self.repository.append_event("general", "system", content=f"event-{index}")
            return int(event["seq"])

        with ThreadPoolExecutor(max_workers=8) as executor:
            sequences = list(executor.map(append, range(40)))

        self.assertEqual(sorted(sequences), list(range(2, 42)))
        self.assertEqual(self.repository.latest_event_sequence("general"), 41)

    def test_vote_query_uses_poll_and_sequence_index(self) -> None:
        self.repository.create_room("vote-plan")
        poll = self.repository.append_event(
            "vote-plan",
            "message_final",
            participant_id="host-a",
            participant_type="human",
            message_kind="vote",
            vote_question="Choose",
            vote_options=["A", "B"],
        )
        ballot = self.repository.append_event(
            "vote-plan",
            "message_final",
            participant_id="guest-a",
            participant_type="human",
            message_kind="vote_cast",
            vote_id=poll["id"],
            vote_choice="A",
        )

        vote_events = self.repository.vote_events("vote-plan", str(poll["id"]))
        with psycopg.connect(self.test_dsn) as connection:
            connection.execute("SET LOCAL enable_seqscan = off")
            plan = connection.execute(
                f"EXPLAIN (COSTS OFF, FORMAT JSON) {_VOTE_BALLOT_EVENTS_QUERY}",
                (
                    "vote-plan",
                    "visible",
                    str(poll["id"]),
                    int(poll["seq"]),
                ),
            ).fetchone()[0]

        self.assertEqual(
            [event["id"] for event in vote_events],
            [poll["id"], ballot["id"]],
        )
        self.assertIn("idx_events_vote_ballots", repr(plan))

    def test_command_unit_uses_one_real_pool_checkout_for_transaction_reads(self) -> None:
        self.repository.create_room("command-connection")
        before = int(
            self.repository.public_diagnostics()["pool"]["stats"].get("requests_num", 0)
        )

        with RoomCommandUnitOfWork(
            self.repository,
            room_id="command-connection",
            principal_id="host-a",
            request_id="request-a",
            action="agent.configure",
            payload={"display_name": "Agent A"},
        ) as unit:
            participant, _created = unit.upsert_participant(
                {
                    "participant_id": "agent-a",
                    "display_name": "Agent A",
                    "participant_type": "agent",
                    "status": "joined",
                }
            )
            self.assertEqual(unit.participant("agent-a"), participant)
            self.assertEqual(
                self.repository.room("command-connection")["room_id"],
                "command-connection",
            )
            unit.build_ack({"participant": participant})
            unit.record_ack()

        after = int(
            self.repository.public_diagnostics()["pool"]["stats"].get("requests_num", 0)
        )
        self.assertEqual(after - before, 1)


def _dsn_with_search_path(dsn: str, schema_name: str) -> str:
    separator = "&" if "?" in dsn else "?"
    option = quote(f"-csearch_path={schema_name}", safe="")
    return f"{dsn}{separator}options={option}"


if __name__ == "__main__":
    unittest.main()
