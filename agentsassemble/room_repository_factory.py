from __future__ import annotations

import importlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentsassemble.persistence.postgres.schema import (
    PostgresRoomSchemaNotReady,
    require_postgres_room_schema,
)
from agentsassemble.room.repository import RoomRepository
from agentsassemble.persistence.local.room.repository import RoomStore


ROOM_REPOSITORY_BACKENDS = frozenset({"sqlite", "postgresql"})
DEFAULT_POSTGRES_DSN_ENV = "AGENTSASSEMBLE_ROOM_DATABASE_URL"
_ENVIRONMENT_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class RoomRepositoryConfigurationError(ValueError):
    """The requested room repository cannot be configured safely."""


class RoomRepositoryUnavailable(RuntimeError):
    """The configured repository backend is unavailable in this installation."""


@dataclass(frozen=True)
class RoomRepositorySettings:
    backend: str = "sqlite"
    postgres_dsn: str = field(default="", repr=False)
    postgres_dsn_env: str = DEFAULT_POSTGRES_DSN_ENV

    def __post_init__(self) -> None:
        backend = str(self.backend or "").strip().lower()
        if backend not in ROOM_REPOSITORY_BACKENDS:
            choices = ", ".join(sorted(ROOM_REPOSITORY_BACKENDS))
            raise RoomRepositoryConfigurationError(
                f"Unsupported room repository backend {backend!r}; choose one of: {choices}."
            )
        dsn_env = str(self.postgres_dsn_env or "").strip()
        if not _ENVIRONMENT_NAME.fullmatch(dsn_env):
            raise RoomRepositoryConfigurationError(
                "PostgreSQL DSN environment variable name must contain only uppercase letters, digits, and underscores."
            )
        object.__setattr__(self, "backend", backend)
        object.__setattr__(self, "postgres_dsn", str(self.postgres_dsn or "").strip())
        object.__setattr__(self, "postgres_dsn_env", dsn_env)

    @classmethod
    def from_environment(
        cls,
        *,
        backend: str = "sqlite",
        postgres_dsn_env: str = DEFAULT_POSTGRES_DSN_ENV,
        environment: Mapping[str, str] | None = None,
    ) -> RoomRepositorySettings:
        source = os.environ if environment is None else environment
        clean_env_name = str(postgres_dsn_env or "").strip()
        dsn = str(source.get(clean_env_name) or "").strip() if clean_env_name else ""
        return cls(
            backend=backend,
            postgres_dsn=dsn,
            postgres_dsn_env=clean_env_name,
        )

    def public_diagnostics(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "postgres_dsn_configured": bool(self.postgres_dsn) if self.backend == "postgresql" else False,
            "postgres_dsn_env": self.postgres_dsn_env if self.backend == "postgresql" else "",
        }


def build_room_repository(
    output_root: Path,
    settings: RoomRepositorySettings,
    *,
    postgres_database: Any | None = None,
) -> RoomRepository:
    if settings.backend == "sqlite":
        if postgres_database is not None:
            raise RoomRepositoryConfigurationError(
                "A PostgreSQL application database cannot be used with SQLite room storage."
            )
        return RoomStore(output_root)
    if not settings.postgres_dsn:
        raise RoomRepositoryConfigurationError(
            f"PostgreSQL room storage requires {settings.postgres_dsn_env} to be set."
        )

    repository_type = _postgres_repository_type()
    if postgres_database is None:
        try:
            require_postgres_room_schema(settings.postgres_dsn)
        except PostgresRoomSchemaNotReady as error:
            raise RoomRepositoryUnavailable(str(error)) from error
        return repository_type(
            settings.postgres_dsn,
            output_root=Path(output_root),
            migrate=False,
        )
    return repository_type(
        database=postgres_database,
        output_root=Path(output_root),
        migrate=False,
    )


def build_postgres_application_database(settings: RoomRepositorySettings) -> Any:
    """Build the one application-owned PostgreSQL connection boundary."""

    if settings.backend != "postgresql":
        raise RoomRepositoryConfigurationError(
            "A PostgreSQL application database requires the PostgreSQL backend."
        )
    if not settings.postgres_dsn:
        raise RoomRepositoryConfigurationError(
            f"PostgreSQL room storage requires {settings.postgres_dsn_env} to be set."
        )
    database_type = _postgres_application_database_type()
    try:
        return database_type(settings.postgres_dsn)
    except PostgresRoomSchemaNotReady as error:
        raise RoomRepositoryUnavailable(str(error)) from error


def _postgres_repository_type() -> Any:
    try:
        module = importlib.import_module(
            "agentsassemble.persistence.postgres.room.repository"
        )
    except ModuleNotFoundError as error:
        if error.name in {"psycopg", "psycopg_pool"}:
            raise RoomRepositoryUnavailable(
                "PostgreSQL room storage requires the optional 'postgres' dependencies. "
                "Install AgentsAssemble with the postgres extra."
            ) from error
        raise
    repository_type = getattr(module, "PostgresRoomRepository", None)
    if repository_type is None:
        raise RoomRepositoryUnavailable(
            "PostgreSQL room storage is unavailable because its repository adapter is missing."
        )
    return repository_type


def _postgres_application_database_type() -> Any:
    try:
        module = importlib.import_module(
            "agentsassemble.persistence.postgres.application_database"
        )
    except ModuleNotFoundError as error:
        if error.name in {"psycopg", "psycopg_pool"}:
            raise RoomRepositoryUnavailable(
                "PostgreSQL room storage requires the optional 'postgres' dependencies. "
                "Install AgentsAssemble with the postgres extra."
            ) from error
        raise
    database_type = getattr(module, "PostgresApplicationDatabase", None)
    if database_type is None:
        raise RoomRepositoryUnavailable(
            "PostgreSQL room storage is unavailable because its application database adapter is missing."
        )
    return database_type
