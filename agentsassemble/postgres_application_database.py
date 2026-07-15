"""Application-owned PostgreSQL pool and transaction boundary."""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from typing import Any, Protocol

from agentsassemble.postgres_connection_pool import (
    BoundedPostgresConnectionPool,
    PoolFactory,
    PostgresPoolSettings,
)
from agentsassemble.postgres_room_schema import (
    POSTGRES_ROOM_SCHEMA_REVISION,
    require_postgres_room_schema,
)


SchemaChecker = Callable[[str], None]


class PostgresConnectionProvider(Protocol):
    """Connection surface shared by application-owned and standalone pools."""

    def connection(self) -> AbstractContextManager[Any]: ...

    def public_diagnostics(self) -> dict[str, object]: ...


class PostgresApplicationDatabase:
    """Own one verified PostgreSQL pool for all application repositories.

    The DSN is used only during construction. Public representations and
    diagnostics expose configuration state and bounded numeric pool metrics,
    never connection material.
    """

    def __init__(
        self,
        dsn: str,
        *,
        pool_settings: PostgresPoolSettings | None = None,
        pool_factory: PoolFactory | None = None,
        schema_checker: SchemaChecker = require_postgres_room_schema,
        connection_kwargs: Mapping[str, object] | None = None,
    ) -> None:
        clean_dsn = str(dsn or "").strip()
        if not clean_dsn:
            raise ValueError("PostgreSQL application database requires a database DSN.")
        schema_checker(clean_dsn)
        self._active_connection: ContextVar[Any | None] = ContextVar(
            f"postgres_application_connection_{id(self)}",
            default=None,
        )
        self._pool = BoundedPostgresConnectionPool(
            clean_dsn,
            connection_kwargs=(
                dict(connection_kwargs)
                if connection_kwargs is not None
                else _default_connection_kwargs()
            ),
            settings=pool_settings,
            pool_factory=pool_factory,
        )

    def __repr__(self) -> str:
        return "PostgresApplicationDatabase(configured=True)"

    @contextmanager
    def connection(self) -> Iterator[Any]:
        """Borrow the active unit-of-work connection or one pooled connection."""

        active = self._active_connection.get()
        if active is not None:
            yield active
            return
        with self._pool.connection() as connection:
            yield connection

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        """Run one outer transaction shared by every repository using this owner."""

        active = self._active_connection.get()
        if active is not None:
            yield active
            return
        with self._pool.connection() as connection, connection.transaction():
            token = self._active_connection.set(connection)
            try:
                yield connection
            finally:
                self._active_connection.reset(token)

    def health(self) -> dict[str, object]:
        """Return a redacted live readiness probe plus bounded pool metrics."""

        try:
            with self.connection() as connection:
                row = connection.execute("SELECT 1 AS ready").fetchone()
            ready = bool(row and _row_value(row, "ready") == 1)
        except Exception as error:
            return {
                "backend": "postgresql",
                "status": "error",
                "schema_revision": POSTGRES_ROOM_SCHEMA_REVISION,
                "error_type": type(error).__name__,
                "pool": self._pool.public_diagnostics(),
            }
        return {
            "backend": "postgresql",
            "status": "ready" if ready else "error",
            "schema_revision": POSTGRES_ROOM_SCHEMA_REVISION,
            "error_type": "" if ready else "UnexpectedHealthResult",
            "pool": self._pool.public_diagnostics(),
        }

    def public_diagnostics(self) -> dict[str, object]:
        return {
            "backend": "postgresql",
            "schema_revision": POSTGRES_ROOM_SCHEMA_REVISION,
            "pool": self._pool.public_diagnostics(),
        }

    def close(self) -> None:
        self._pool.close()


def _default_connection_kwargs() -> dict[str, object]:
    from psycopg.rows import dict_row

    return {"row_factory": dict_row}


def _row_value(row: object, key: str) -> object:
    if isinstance(row, Mapping):
        return row.get(key)
    if isinstance(row, (tuple, list)) and row:
        return row[0]
    return None
