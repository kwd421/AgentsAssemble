"""Bounded PostgreSQL connection pool ownership."""
from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol


class PostgresPoolStartupError(RuntimeError):
    """The configured PostgreSQL pool could not become ready."""


class PostgresPoolClosed(RuntimeError):
    """A connection was requested after the repository pool was closed."""


class _ConnectionPool(Protocol):
    @property
    def closed(self) -> bool: ...

    def wait(self, timeout: float) -> None: ...

    def connection(self, timeout: float) -> Any: ...

    def close(self, timeout: float) -> None: ...

    def get_stats(self) -> Mapping[str, object]: ...


PoolFactory = Callable[..., _ConnectionPool]


@dataclass(frozen=True)
class PostgresPoolSettings:
    min_size: int = 1
    max_size: int = 8
    max_waiting: int = 32
    acquire_timeout_seconds: float = 5.0
    startup_timeout_seconds: float = 10.0
    close_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        _require_positive_int("min_size", self.min_size)
        _require_positive_int("max_size", self.max_size)
        _require_positive_int("max_waiting", self.max_waiting)
        if self.min_size > self.max_size:
            raise ValueError("PostgreSQL pool min_size cannot exceed max_size.")
        _require_positive_number("acquire_timeout_seconds", self.acquire_timeout_seconds)
        _require_positive_number("startup_timeout_seconds", self.startup_timeout_seconds)
        _require_positive_number("close_timeout_seconds", self.close_timeout_seconds)


_PUBLIC_POOL_STATS = frozenset(
    {
        "connections_num",
        "connections_ms",
        "requests_num",
        "requests_queued",
        "requests_wait_ms",
        "requests_errors",
        "usage_ms",
        "pool_min",
        "pool_max",
        "pool_size",
        "pool_available",
        "requests_waiting",
    }
)


class BoundedPostgresConnectionPool:
    """Own one bounded psycopg pool without retaining or reporting its DSN."""

    def __init__(
        self,
        dsn: str,
        *,
        connection_kwargs: Mapping[str, object] | None = None,
        settings: PostgresPoolSettings | None = None,
        pool_factory: PoolFactory | None = None,
    ) -> None:
        clean_dsn = str(dsn or "").strip()
        if not clean_dsn:
            raise ValueError("PostgreSQL connection pool requires a database DSN.")
        self._settings = settings or PostgresPoolSettings()
        self._state_lock = threading.Lock()
        self._closed = False
        factory = pool_factory or _default_pool_factory
        pool: _ConnectionPool | None = None
        try:
            pool = factory(
                conninfo=clean_dsn,
                kwargs=dict(connection_kwargs or {}),
                min_size=self._settings.min_size,
                max_size=self._settings.max_size,
                max_waiting=self._settings.max_waiting,
                timeout=self._settings.acquire_timeout_seconds,
                open=True,
                name="agentsassemble-room",
            )
            pool.wait(timeout=self._settings.startup_timeout_seconds)
        except Exception:
            cleanup_failed = False
            if pool is not None:
                try:
                    pool.close(timeout=self._settings.close_timeout_seconds)
                except Exception:
                    cleanup_failed = True
            message = (
                "PostgreSQL room connection pool could not be initialized "
                "within its startup boundary."
            )
            if cleanup_failed:
                message += " Partial pool cleanup also failed."
            raise PostgresPoolStartupError(message) from None
        self._pool = pool

    @contextmanager
    def connection(self) -> Iterator[Any]:
        with self._state_lock:
            if self._closed:
                raise PostgresPoolClosed("PostgreSQL room connection pool is closed.")
            pool = self._pool
        with pool.connection(
            timeout=self._settings.acquire_timeout_seconds
        ) as connection:
            yield connection

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            pool = self._pool
        pool.close(timeout=self._settings.close_timeout_seconds)

    def public_diagnostics(self) -> dict[str, object]:
        with self._state_lock:
            closed = self._closed
            pool = self._pool
        raw_stats = pool.get_stats()
        stats = {
            key: value
            for key, value in raw_stats.items()
            if key in _PUBLIC_POOL_STATS and _is_public_number(value)
        }
        return {
            "closed": closed,
            "min_size": self._settings.min_size,
            "max_size": self._settings.max_size,
            "max_waiting": self._settings.max_waiting,
            "acquire_timeout_seconds": self._settings.acquire_timeout_seconds,
            "stats": stats,
        }


def _default_pool_factory(**kwargs: object) -> _ConnectionPool:
    from psycopg_pool import ConnectionPool

    return ConnectionPool(**kwargs)


def _require_positive_int(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"PostgreSQL pool {name} must be a positive integer.")


def _require_positive_number(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"PostgreSQL pool {name} must be greater than zero.")


def _is_public_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


__all__ = [
    "BoundedPostgresConnectionPool",
    "PoolFactory",
    "PostgresPoolClosed",
    "PostgresPoolSettings",
    "PostgresPoolStartupError",
]
