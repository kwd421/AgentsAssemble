from __future__ import annotations

import importlib.util
import os
import unittest
from contextlib import nullcontext
from uuid import uuid4

from tests.test_room_invite_repository import InviteSessionRepositoryContract

_POSTGRES_DSN = os.environ.get("AGENTSASSEMBLE_TEST_POSTGRES_DSN", "").strip()
_PSYCOPG_AVAILABLE = importlib.util.find_spec("psycopg") is not None

if _PSYCOPG_AVAILABLE:
    import psycopg
    from psycopg import sql

    from agentsassemble.postgres_invite_repository import PostgresInviteSessionRepository
    from agentsassemble.postgres_room_schema import upgrade_postgres_room_schema


class _FakeConnection:
    def transaction(self):
        return nullcontext()


class _FakePool:
    def __init__(self, kwargs: dict[str, object]) -> None:
        self.kwargs = kwargs
        self.closed = False

    def wait(self, timeout: float) -> None:
        return

    def connection(self, timeout: float):
        return nullcontext(_FakeConnection())

    def close(self, timeout: float) -> None:
        self.closed = True

    def get_stats(self) -> dict[str, object]:
        return {"pool_size": 1, "conninfo": "must-not-leak"}


@unittest.skipUnless(_PSYCOPG_AVAILABLE, "the postgres extra is required")
class PostgresInviteSessionRepositoryPoolTests(unittest.TestCase):
    def test_repository_diagnostics_do_not_disclose_dsn(self) -> None:
        pools: list[_FakePool] = []

        def factory(**kwargs: object) -> _FakePool:
            pool = _FakePool(dict(kwargs))
            pools.append(pool)
            return pool

        repository = PostgresInviteSessionRepository(
            "postgresql://secret-user:secret-password@example.invalid/rooms",
            pool_factory=factory,
        )
        try:
            diagnostics = repository.public_diagnostics()
            self.assertEqual(
                repr(repository),
                "PostgresInviteSessionRepository(configured=True)",
            )
            self.assertNotIn("secret-user", str(diagnostics))
            self.assertNotIn("secret-password", str(diagnostics))
            self.assertNotIn("must-not-leak", str(diagnostics))
        finally:
            repository.close()

        self.assertTrue(pools[0].closed)


@unittest.skipUnless(
    _PSYCOPG_AVAILABLE and _POSTGRES_DSN,
    "AGENTSASSEMBLE_TEST_POSTGRES_DSN and the postgres extra are required",
)
class PostgresInviteSessionRepositoryContractTests(
    InviteSessionRepositoryContract,
    unittest.TestCase,
):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema_name = f"agentsassemble_invite_{uuid4().hex[:12]}"
        with psycopg.connect(_POSTGRES_DSN, autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema_name))
            )
        cls.test_dsn = _dsn_with_search_path(_POSTGRES_DSN, cls.schema_name)
        upgrade_postgres_room_schema(cls.test_dsn)

    @classmethod
    def tearDownClass(cls) -> None:
        with psycopg.connect(_POSTGRES_DSN, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema_name))
            )

    def setUp(self) -> None:
        with psycopg.connect(self.test_dsn) as connection:
            connection.execute("TRUNCATE TABLE deleted_rooms, rooms CASCADE")
            for room_id in ("room-a", "room-b"):
                connection.execute(
                    """INSERT INTO rooms(
                           room_id, label, status, archived, updated_at, data_json
                       ) VALUES(%s, %s, 'active', FALSE, NOW(), '{}'::jsonb)""",
                    (room_id, room_id),
                )
        self.repository = PostgresInviteSessionRepository(self.test_dsn)

    def tearDown(self) -> None:
        self.repository.close()


def _dsn_with_search_path(dsn: str, schema_name: str) -> str:
    separator = "&" if "?" in dsn else "?"
    return f"{dsn}{separator}options=-csearch_path%3D{schema_name}"


if __name__ == "__main__":
    unittest.main()
