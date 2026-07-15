from __future__ import annotations

import importlib.util
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from urllib.parse import quote
from uuid import uuid4

from tests.identity_repository_contract import IdentityRepositoryContractMixin


_POSTGRES_DSN = os.environ.get("AGENTSASSEMBLE_TEST_POSTGRES_DSN", "").strip()
_PSYCOPG_AVAILABLE = importlib.util.find_spec("psycopg") is not None

if _PSYCOPG_AVAILABLE:
    import psycopg
    from psycopg import sql

    from agentsassemble.identity_store import IdentityBackend
    from agentsassemble.postgres_identity_repository import PostgresIdentityRepository
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
class PostgresIdentityRepositoryPoolTests(unittest.TestCase):
    def test_repository_diagnostics_do_not_disclose_dsn(self) -> None:
        pools: list[_FakePool] = []

        def factory(**kwargs: object) -> _FakePool:
            pool = _FakePool(dict(kwargs))
            pools.append(pool)
            return pool

        repository = PostgresIdentityRepository(
            "postgresql://secret-user:secret-password@example.invalid/rooms",
            pool_factory=factory,
        )
        try:
            diagnostics = repository.public_diagnostics()
            self.assertEqual(
                repr(repository),
                "PostgresIdentityRepository(configured=True)",
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
class PostgresIdentityRepositoryContractTests(
    IdentityRepositoryContractMixin,
    unittest.TestCase,
):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema_name = f"agentsassemble_identity_{uuid4().hex[:12]}"
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
        self.repository = PostgresIdentityRepository(self.test_dsn)
        self.repository.clear()

    def tearDown(self) -> None:
        self.repository.close()

    def test_repository_implements_identity_protocol(self) -> None:
        self.assertIsInstance(self.repository, IdentityBackend)

    def test_concurrent_credential_resolution_creates_one_user(self) -> None:
        def resolve(_index: int) -> str:
            user = self.repository.resolve_credential_user("device:concurrent-contract")
            return str(user["user_id"])

        with ThreadPoolExecutor(max_workers=8) as executor:
            user_ids = list(executor.map(resolve, range(24)))

        self.assertEqual(len(set(user_ids)), 1)
        self.assertEqual(self.repository.count_users(), 1)


def _dsn_with_search_path(dsn: str, schema_name: str) -> str:
    separator = "&" if "?" in dsn else "?"
    option = quote(f"-csearch_path={schema_name}", safe="")
    return f"{dsn}{separator}options={option}"


if __name__ == "__main__":
    unittest.main()
