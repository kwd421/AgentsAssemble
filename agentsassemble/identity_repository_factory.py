"""Select identity persistence alongside the canonical room backend."""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from agentsassemble.identity_store import IdentityBackend, identity_store_for_output_root
from agentsassemble.room_repository_factory import (
    RoomRepositoryConfigurationError,
    RoomRepositorySettings,
    RoomRepositoryUnavailable,
)


def build_identity_repository(
    output_root: Path,
    settings: RoomRepositorySettings,
    *,
    postgres_database: Any | None = None,
) -> IdentityBackend:
    if settings.backend == "sqlite":
        if postgres_database is not None:
            raise RoomRepositoryConfigurationError(
                "A PostgreSQL application database cannot be used with SQLite identity storage."
            )
        return identity_store_for_output_root(output_root)
    if not settings.postgres_dsn:
        raise RoomRepositoryConfigurationError(
            f"PostgreSQL identity storage requires {settings.postgres_dsn_env} to be set."
        )
    repository_type = _postgres_repository_type()
    if postgres_database is not None:
        return repository_type(database=postgres_database)
    return repository_type(settings.postgres_dsn)


def _postgres_repository_type() -> Any:
    try:
        module = importlib.import_module(
            "agentsassemble.persistence.postgres.identity.repository"
        )
    except ModuleNotFoundError as error:
        if error.name in {"psycopg", "psycopg_pool"}:
            raise RoomRepositoryUnavailable(
                "PostgreSQL identity storage requires the optional 'postgres' dependencies. "
                "Install AgentsAssemble with the postgres extra."
            ) from error
        raise
    repository_type = getattr(module, "PostgresIdentityRepository", None)
    if repository_type is None:
        raise RoomRepositoryUnavailable(
            "PostgreSQL identity storage is unavailable because its repository adapter is missing."
        )
    return repository_type
