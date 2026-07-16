"""Shared PostgreSQL schema readiness and migration boundary."""
from __future__ import annotations

from pathlib import Path


POSTGRES_ROOM_SCHEMA_REVISION = "0009_resumable_operator_pairing"
POSTGRES_ROOM_AUTHORITY_ID = "canonical-room-repository"
POSTGRES_ROOM_REQUIRED_TABLES = (
    "rooms",
    "room_settings",
    "participants",
    "agent_sessions",
    "room_events",
    "command_results",
    "deleted_rooms",
    "agent_attention_state",
    "attention_jobs",
    "attention_leases",
    "scheduled_wakeups",
    "conversation_obligations",
    "room_repository_authority",
    "room_invite_authority",
    "room_invites",
    "room_invite_used_nonces",
    "room_access_sessions",
    "identity_users",
    "identity_credentials",
    "identity_operator_pairings",
    "identity_memberships",
    "identity_room_registry",
    "identity_room_user_preferences",
    "identity_usage_events",
)


class PostgresRoomMigrationError(RuntimeError):
    """PostgreSQL room schema migration did not complete."""


class PostgresRoomSchemaNotReady(RuntimeError):
    """PostgreSQL exists but is not an activated canonical room authority."""


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


def require_postgres_room_schema(dsn: str) -> None:
    """Fail unless the current schema and explicit authority marker are ready."""

    clean_dsn = str(dsn or "").strip()
    if not clean_dsn:
        raise PostgresRoomSchemaNotReady("PostgreSQL room storage requires a database DSN.")
    try:
        import psycopg
    except ModuleNotFoundError as error:
        raise PostgresRoomSchemaNotReady(
            "PostgreSQL room storage requires the optional 'postgres' dependencies."
        ) from error

    try:
        with psycopg.connect(clean_dsn) as connection:
            missing = [
                table
                for table in POSTGRES_ROOM_REQUIRED_TABLES
                if connection.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is None
            ]
            revision = ""
            alembic_exists = connection.execute(
                "SELECT to_regclass('alembic_version')"
            ).fetchone()[0]
            if alembic_exists is not None:
                row = connection.execute(
                    "SELECT version_num FROM alembic_version LIMIT 1"
                ).fetchone()
                revision = str((row or ("",))[0] or "")
            authority_active = False
            if "room_repository_authority" not in missing:
                authority_active = connection.execute(
                    "SELECT 1 FROM room_repository_authority WHERE authority_id = %s",
                    (POSTGRES_ROOM_AUTHORITY_ID,),
                ).fetchone() is not None
    except PostgresRoomSchemaNotReady:
        raise
    except Exception as error:
        sqlstate = str(getattr(error, "sqlstate", "") or "")
        suffix = f" (SQLSTATE {sqlstate})" if sqlstate else ""
        raise PostgresRoomSchemaNotReady(
            f"PostgreSQL room schema check failed: {type(error).__name__}{suffix}."
        ) from None

    if missing or revision != POSTGRES_ROOM_SCHEMA_REVISION:
        raise PostgresRoomSchemaNotReady(
            "PostgreSQL room schema is not ready; run "
            "'assemble room migrate-postgres --apply' before selecting this backend."
        )
    if not authority_active:
        raise PostgresRoomSchemaNotReady(
            "PostgreSQL room authority is not activated; run "
            "'assemble room migrate-postgres --apply' before selecting this backend."
        )


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
    return Path(__file__).resolve().parents[2] / "migrations"
