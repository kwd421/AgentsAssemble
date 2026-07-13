from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote
from uuid import uuid4

from agentsassemble.room_repository import RoomRepository
from agentsassemble.room_repository_factory import RoomRepositorySettings, build_room_repository
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

    def test_postgres_repository_implements_repository_protocol(self) -> None:
        self.assertIsInstance(self.repository, RoomRepository)

    def test_repository_representation_does_not_disclose_dsn(self) -> None:
        self.assertEqual(repr(self.repository), "PostgresRoomRepository(configured=True)")
        self.assertNotIn(self.test_dsn, repr(self.repository))

    def test_repository_constructor_does_not_migrate_by_default(self) -> None:
        with patch(
            "agentsassemble.postgres_room_repository.upgrade_postgres_room_schema"
        ) as upgrade:
            PostgresRoomRepository(self.test_dsn)

        upgrade.assert_not_called()

    def test_factory_builds_postgres_without_opening_sqlite(self) -> None:
        output_root = Path(self._temporary_directory.name) / "factory"
        repository = build_room_repository(
            output_root,
            RoomRepositorySettings(backend="postgresql", postgres_dsn=self.test_dsn),
        )

        self.assertIsInstance(repository, PostgresRoomRepository)
        self.assertFalse((output_root / "rooms" / "rooms.sqlite3").exists())

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


def _dsn_with_search_path(dsn: str, schema_name: str) -> str:
    separator = "&" if "?" in dsn else "?"
    option = quote(f"-csearch_path={schema_name}", safe="")
    return f"{dsn}{separator}options={option}"


if __name__ == "__main__":
    unittest.main()
