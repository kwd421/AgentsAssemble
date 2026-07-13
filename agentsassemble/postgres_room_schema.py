from __future__ import annotations

from pathlib import Path


class PostgresRoomMigrationError(RuntimeError):
    """PostgreSQL room schema migration did not complete."""


def upgrade_postgres_room_schema(dsn: str) -> None:
    clean_dsn = str(dsn or "").strip()
    if not clean_dsn:
        raise PostgresRoomMigrationError("PostgreSQL room schema migration requires a database DSN.")

    try:
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool
    except ModuleNotFoundError as error:
        raise PostgresRoomMigrationError(
            "PostgreSQL room schema migration requires the optional 'postgres' dependencies."
        ) from error

    config = Config()
    config.set_main_option("script_location", str(_migration_script_path()))
    engine = create_engine(
        _sqlalchemy_psycopg_url(clean_dsn),
        future=True,
        hide_parameters=True,
        poolclass=NullPool,
    )
    try:
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    except Exception as error:
        error_type = type(error).__name__
        sqlstate = str(getattr(error, "sqlstate", "") or "")
        suffix = f" (SQLSTATE {sqlstate})" if sqlstate else ""
        raise PostgresRoomMigrationError(
            f"PostgreSQL room schema migration failed: {error_type}{suffix}."
        ) from None
    finally:
        engine.dispose()


def _sqlalchemy_psycopg_url(dsn: str) -> str:
    if dsn.startswith("postgresql+psycopg://"):
        return dsn
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn.removeprefix("postgresql://")
    if dsn.startswith("postgres://"):
        return "postgresql+psycopg://" + dsn.removeprefix("postgres://")
    raise PostgresRoomMigrationError(
        "PostgreSQL room DSN must use the postgres:// or postgresql:// scheme."
    )


def _migration_script_path() -> Path:
    return Path(__file__).resolve().parent / "migrations"
