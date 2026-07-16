from __future__ import annotations

import unittest
from contextlib import contextmanager
from importlib.util import find_spec

from agentsassemble.postgres_connection_pool import (
    BoundedPostgresConnectionPool,
    PostgresPoolClosed,
    PostgresPoolSettings,
    PostgresPoolStartupError,
    _default_pool_factory,
)
from agentsassemble.persistence.postgres.connection_pool import (
    BoundedPostgresConnectionPool as OwnedBoundedPostgresConnectionPool,
)


class _FakePool:
    def __init__(
        self,
        constructor_kwargs: dict[str, object],
        *,
        wait_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.constructor_kwargs = constructor_kwargs
        self.wait_error = wait_error
        self.close_error = close_error
        self.wait_timeouts: list[float] = []
        self.connection_timeouts: list[float] = []
        self.close_timeouts: list[float] = []
        self.closed = False
        self.connection_value = object()
        self.stats: dict[str, object] = {
            "pool_size": 3,
            "pool_available": 2,
            "requests_waiting": 1,
        }

    def wait(self, timeout: float) -> None:
        self.wait_timeouts.append(timeout)
        if self.wait_error is not None:
            raise self.wait_error

    @contextmanager
    def connection(self, timeout: float):
        self.connection_timeouts.append(timeout)
        yield self.connection_value

    def close(self, timeout: float) -> None:
        self.close_timeouts.append(timeout)
        if self.close_error is not None:
            raise self.close_error
        self.closed = True

    def get_stats(self) -> dict[str, object]:
        return dict(self.stats)


class PostgresConnectionPoolTests(unittest.TestCase):
    def test_root_import_is_an_explicit_compatibility_export(self) -> None:
        self.assertIs(
            BoundedPostgresConnectionPool,
            OwnedBoundedPostgresConnectionPool,
        )

    def test_settings_require_bounded_positive_values(self) -> None:
        invalid_settings = (
            {"min_size": 0},
            {"max_size": 0},
            {"max_waiting": 0},
            {"min_size": 3, "max_size": 2},
            {"acquire_timeout_seconds": 0},
            {"startup_timeout_seconds": -1},
            {"close_timeout_seconds": False},
        )

        for updates in invalid_settings:
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                PostgresPoolSettings(**updates)

    def test_pool_opens_once_with_bounded_configuration_and_reuses_it(self) -> None:
        created: list[_FakePool] = []

        def factory(**kwargs: object) -> _FakePool:
            pool = _FakePool(dict(kwargs))
            created.append(pool)
            return pool

        settings = PostgresPoolSettings(
            min_size=2,
            max_size=6,
            max_waiting=12,
            acquire_timeout_seconds=3.5,
            startup_timeout_seconds=7.0,
            close_timeout_seconds=2.5,
        )
        row_factory = object()
        pool = BoundedPostgresConnectionPool(
            "postgresql://user:secret@example.invalid/rooms",
            connection_kwargs={"row_factory": row_factory},
            settings=settings,
            pool_factory=factory,
        )

        with pool.connection() as first, pool.connection() as second:
            self.assertIs(first, created[0].connection_value)
            self.assertIs(second, created[0].connection_value)

        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].wait_timeouts, [7.0])
        self.assertEqual(created[0].connection_timeouts, [3.5, 3.5])
        self.assertEqual(
            created[0].constructor_kwargs,
            {
                "conninfo": "postgresql://user:secret@example.invalid/rooms",
                "kwargs": {"row_factory": row_factory},
                "min_size": 2,
                "max_size": 6,
                "max_waiting": 12,
                "timeout": 3.5,
                "open": True,
                "name": "agentsassemble-room",
            },
        )

    def test_startup_failure_closes_partial_pool_without_disclosing_dsn(self) -> None:
        created: list[_FakePool] = []

        def factory(**kwargs: object) -> _FakePool:
            pool = _FakePool(dict(kwargs), wait_error=RuntimeError("secret-password"))
            created.append(pool)
            return pool

        with self.assertRaises(PostgresPoolStartupError) as raised:
            BoundedPostgresConnectionPool(
                "postgresql://secret-user:secret-password@example.invalid/rooms",
                pool_factory=factory,
            )

        self.assertEqual(created[0].close_timeouts, [5.0])
        self.assertNotIn("secret-user", str(raised.exception))
        self.assertNotIn("secret-password", str(raised.exception))

    def test_startup_reports_partial_pool_cleanup_failure_without_raw_error(self) -> None:
        def factory(**kwargs: object) -> _FakePool:
            return _FakePool(
                dict(kwargs),
                wait_error=RuntimeError("startup-secret"),
                close_error=RuntimeError("cleanup-secret"),
            )

        with self.assertRaises(PostgresPoolStartupError) as raised:
            BoundedPostgresConnectionPool("postgresql://configured", pool_factory=factory)

        self.assertIn("Partial pool cleanup also failed", str(raised.exception))
        self.assertNotIn("startup-secret", str(raised.exception))
        self.assertNotIn("cleanup-secret", str(raised.exception))

    def test_close_is_idempotent_and_rejects_new_connections(self) -> None:
        created: list[_FakePool] = []

        def factory(**kwargs: object) -> _FakePool:
            pool = _FakePool(dict(kwargs))
            created.append(pool)
            return pool

        pool = BoundedPostgresConnectionPool("postgresql://configured", pool_factory=factory)

        pool.close()
        pool.close()

        self.assertEqual(created[0].close_timeouts, [5.0])
        with self.assertRaises(PostgresPoolClosed):
            with pool.connection():
                pass

    def test_public_diagnostics_allowlist_numeric_pool_stats_only(self) -> None:
        created: list[_FakePool] = []

        def factory(**kwargs: object) -> _FakePool:
            pool = _FakePool(dict(kwargs))
            pool.stats.update(
                {
                    "requests_errors": 4,
                    "conninfo": "postgresql://secret-user:secret-password@example.invalid/rooms",
                    "dsn": "secret-password",
                    "pool_min": True,
                    "unexpected": 99,
                }
            )
            created.append(pool)
            return pool

        pool = BoundedPostgresConnectionPool("postgresql://configured", pool_factory=factory)

        diagnostics = pool.public_diagnostics()

        self.assertEqual(
            diagnostics["stats"],
            {
                "pool_size": 3,
                "pool_available": 2,
                "requests_waiting": 1,
                "requests_errors": 4,
            },
        )
        self.assertNotIn("secret", str(diagnostics))
        self.assertNotIn("conninfo", str(diagnostics))
        self.assertNotIn("dsn", str(diagnostics))

    @unittest.skipUnless(find_spec("psycopg_pool"), "the postgres extra is required")
    def test_default_factory_matches_installed_psycopg_pool_api(self) -> None:
        pool = _default_pool_factory(
            conninfo="postgresql://example.invalid/rooms",
            kwargs={},
            min_size=0,
            max_size=1,
            max_waiting=1,
            timeout=1.0,
            open=False,
            name="agentsassemble-pool-api-test",
        )
        try:
            self.assertTrue(pool.closed)
        finally:
            pool.close(timeout=1.0)


if __name__ == "__main__":
    unittest.main()
