"""Select invite/session persistence alongside the canonical room backend."""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from agentsassemble.room_invite_repository import (
    InviteSessionRepository,
    JsonInviteSessionRepository,
)
from agentsassemble.room_repository_factory import (
    RoomRepositoryConfigurationError,
    RoomRepositorySettings,
    RoomRepositoryUnavailable,
)


def build_invite_session_repository(
    output_root: Path,
    settings: RoomRepositorySettings,
    *,
    postgres_database: Any | None = None,
) -> InviteSessionRepository:
    if settings.backend == "sqlite":
        if postgres_database is not None:
            raise RoomRepositoryConfigurationError(
                "A PostgreSQL application database cannot be used with SQLite invite storage."
            )
        path = Path(output_root) / ".agentsassemble" / "room-invite-state.json"
        return JsonInviteSessionRepository(path)
    if not settings.postgres_dsn:
        raise RoomRepositoryConfigurationError(
            f"PostgreSQL invite storage requires {settings.postgres_dsn_env} to be set."
        )
    repository_type = _postgres_repository_type()
    if postgres_database is not None:
        return repository_type(database=postgres_database)
    return repository_type(settings.postgres_dsn)


def _postgres_repository_type() -> Any:
    try:
        module = importlib.import_module("agentsassemble.postgres_invite_repository")
    except ModuleNotFoundError as error:
        if error.name in {"psycopg", "psycopg_pool"}:
            raise RoomRepositoryUnavailable(
                "PostgreSQL invite storage requires the optional 'postgres' dependencies. "
                "Install AgentsAssemble with the postgres extra."
            ) from error
        raise
    repository_type = getattr(module, "PostgresInviteSessionRepository", None)
    if repository_type is None:
        raise RoomRepositoryUnavailable(
            "PostgreSQL invite storage is unavailable because its repository adapter is missing."
        )
    return repository_type
