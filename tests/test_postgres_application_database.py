from __future__ import annotations

import unittest
from contextlib import contextmanager

from agentsassemble.persistence.postgres.application_database import (
    PostgresApplicationDatabase as OwnedPostgresApplicationDatabase,
)
from agentsassemble.postgres_application_database import PostgresApplicationDatabase
from agentsassemble.postgres_connection_pool import PostgresPoolClosed
from agentsassemble.postgres_room_schema import POSTGRES_ROOM_SCHEMA_REVISION


class _FakeConnection:
    def __init__(self) -> None:
        self.transaction_entries = 0
        self.executed: list[str] = []
        self.health_error: Exception | None = None

    @contextmanager
    def transaction(self):
        self.transaction_entries += 1
        yield

    def execute(self, query: str):
        self.executed.append(query)
        if self.health_error is not None:
            raise self.health_error
        return self

    def fetchone(self) -> dict[str, int]:
        return {"ready": 1}


class _FakePool:
    def __init__(self, kwargs: dict[str, object]) -> None:
        self.kwargs = kwargs
        self.connection_value = _FakeConnection()
        self.connection_count = 0
        self.close_count = 0
        self.closed = False

    def wait(self, timeout: float) -> None:
        return

    @contextmanager
    def connection(self, timeout: float):
        if self.closed:
            raise RuntimeError("fake pool is closed")
        self.connection_count += 1
        yield self.connection_value

    def close(self, timeout: float) -> None:
        self.close_count += 1
        self.closed = True

    def get_stats(self) -> dict[str, object]:
        return {"pool_size": 1, "conninfo": "must-not-leak"}


class PostgresApplicationDatabaseTests(unittest.TestCase):
    def test_root_import_is_an_explicit_compatibility_export(self) -> None:
        self.assertIs(
            PostgresApplicationDatabase,
            OwnedPostgresApplicationDatabase,
        )

    def _database(self) -> tuple[PostgresApplicationDatabase, _FakePool, list[str]]:
        pools: list[_FakePool] = []
        checked: list[str] = []

        def factory(**kwargs: object) -> _FakePool:
            pool = _FakePool(dict(kwargs))
            pools.append(pool)
            return pool

        database = PostgresApplicationDatabase(
            "postgresql://secret-user:secret-password@example.invalid/rooms",
            pool_factory=factory,
            schema_checker=checked.append,
            connection_kwargs={"row_factory": "fake"},
        )
        return database, pools[0], checked

    def test_checks_schema_before_opening_one_pool(self) -> None:
        database, pool, checked = self._database()
        try:
            self.assertEqual(
                checked,
                ["postgresql://secret-user:secret-password@example.invalid/rooms"],
            )
            self.assertEqual(pool.kwargs["kwargs"], {"row_factory": "fake"})
            self.assertNotIn("secret", repr(database))
            self.assertNotIn("secret", str(database.public_diagnostics()))
        finally:
            database.close()

    def test_transaction_reuses_one_connection_for_nested_repository_calls(self) -> None:
        database, pool, _checked = self._database()
        try:
            with database.transaction() as outer:
                with database.connection() as nested_read:
                    self.assertIs(nested_read, outer)
                with database.transaction() as nested_transaction:
                    self.assertIs(nested_transaction, outer)
        finally:
            database.close()

        self.assertEqual(pool.connection_count, 1)
        self.assertEqual(pool.connection_value.transaction_entries, 1)

    def test_health_reports_readiness_without_disclosing_driver_error(self) -> None:
        database, pool, _checked = self._database()
        try:
            self.assertEqual(database.health()["status"], "ready")
            pool.connection_value.health_error = RuntimeError(
                "secret-password must not escape"
            )
            failed = database.health()
        finally:
            database.close()

        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["error_type"], "RuntimeError")
        self.assertEqual(failed["schema_revision"], POSTGRES_ROOM_SCHEMA_REVISION)
        self.assertNotIn("secret-password", str(failed))

    def test_close_is_idempotent_and_rejects_new_connections(self) -> None:
        database, pool, _checked = self._database()

        database.close()
        database.close()

        self.assertEqual(pool.close_count, 1)
        with self.assertRaises(PostgresPoolClosed):
            with database.connection():
                pass

    def test_schema_failure_does_not_open_a_pool(self) -> None:
        pool_calls = 0

        def factory(**kwargs: object) -> _FakePool:
            nonlocal pool_calls
            pool_calls += 1
            return _FakePool(dict(kwargs))

        def fail_schema(_dsn: str) -> None:
            raise RuntimeError("schema unavailable")

        with self.assertRaisesRegex(RuntimeError, "schema unavailable"):
            PostgresApplicationDatabase(
                "postgresql://configured",
                pool_factory=factory,
                schema_checker=fail_schema,
                connection_kwargs={},
            )

        self.assertEqual(pool_calls, 0)


if __name__ == "__main__":
    unittest.main()
