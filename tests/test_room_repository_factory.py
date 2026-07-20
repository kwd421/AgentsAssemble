from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.application.room_repository_factory import (
    DEFAULT_POSTGRES_DSN_ENV,
    RoomRepositoryConfigurationError,
    RoomRepositorySettings,
    RoomRepositoryUnavailable,
    build_postgres_application_database,
    build_room_repository,
)
from agentsassemble.room_store import RoomStore


class RoomRepositorySettingsTests(unittest.TestCase):
    def test_default_settings_build_sqlite_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = build_room_repository(Path(temp_dir), RoomRepositorySettings())

        self.assertIsInstance(repository, RoomStore)

    def test_postgres_settings_read_dsn_from_named_environment_variable(self) -> None:
        settings = RoomRepositorySettings.from_environment(
            backend="postgresql",
            environment={DEFAULT_POSTGRES_DSN_ENV: "postgresql://secret@example/rooms"},
        )

        self.assertEqual(settings.backend, "postgresql")
        self.assertTrue(settings.public_diagnostics()["postgres_dsn_configured"])
        self.assertNotIn("secret", repr(settings))
        self.assertNotIn("secret", str(settings.public_diagnostics()))

    def test_invalid_backend_is_rejected(self) -> None:
        with self.assertRaisesRegex(RoomRepositoryConfigurationError, "Unsupported room repository backend"):
            RoomRepositorySettings(backend="memory")

    def test_invalid_dsn_environment_name_is_rejected(self) -> None:
        with self.assertRaisesRegex(RoomRepositoryConfigurationError, "environment variable name"):
            RoomRepositorySettings(postgres_dsn_env="room-dsn")

    def test_postgres_without_dsn_fails_instead_of_falling_back_to_sqlite(self) -> None:
        settings = RoomRepositorySettings(backend="postgresql")

        with self.assertRaisesRegex(RoomRepositoryConfigurationError, DEFAULT_POSTGRES_DSN_ENV):
            build_room_repository(Path("."), settings)

    def test_missing_postgres_driver_is_an_explicit_backend_error(self) -> None:
        settings = RoomRepositorySettings(
            backend="postgresql",
            postgres_dsn="postgresql://hidden@example/rooms",
        )
        missing_driver = ModuleNotFoundError("No module named 'psycopg'", name="psycopg")

        with patch("importlib.import_module", side_effect=missing_driver):
            with self.assertRaises(RoomRepositoryUnavailable) as raised:
                build_room_repository(Path("."), settings)

        self.assertNotIn("hidden", str(raised.exception))
        self.assertIn("postgres extra", str(raised.exception))

    def test_postgres_requires_activated_schema_and_disables_constructor_migration(self) -> None:
        class FakePostgresRepository:
            def __init__(self, dsn, *, output_root, migrate):
                self.dsn = dsn
                self.output_root = output_root
                self.migrate = migrate

        settings = RoomRepositorySettings(
            backend="postgresql",
            postgres_dsn="postgresql://hidden@example/rooms",
        )
        with patch(
            "agentsassemble.application.room_repository_factory._postgres_repository_type",
            return_value=FakePostgresRepository,
        ), patch(
            "agentsassemble.application.room_repository_factory.require_postgres_room_schema"
        ) as require_schema:
            repository = build_room_repository(Path("/tmp/output"), settings)

        require_schema.assert_called_once_with("postgresql://hidden@example/rooms")
        self.assertFalse(repository.migrate)

    def test_injected_postgres_database_owns_schema_and_connection_setup(self) -> None:
        database = object()

        class FakePostgresRepository:
            def __init__(self, *, database, output_root, migrate):
                self.database = database
                self.output_root = output_root
                self.migrate = migrate

        settings = RoomRepositorySettings(
            backend="postgresql",
            postgres_dsn="postgresql://hidden@example/rooms",
        )
        with patch(
            "agentsassemble.application.room_repository_factory._postgres_repository_type",
            return_value=FakePostgresRepository,
        ), patch(
            "agentsassemble.application.room_repository_factory.require_postgres_room_schema"
        ) as require_schema:
            repository = build_room_repository(
                Path("/tmp/output"),
                settings,
                postgres_database=database,
            )

        require_schema.assert_not_called()
        self.assertIs(repository.database, database)
        self.assertFalse(repository.migrate)

    def test_application_database_factory_checks_schema_through_one_owner(self) -> None:
        class FakeApplicationDatabase:
            def __init__(self, dsn: str) -> None:
                self.dsn = dsn

        settings = RoomRepositorySettings(
            backend="postgresql",
            postgres_dsn="postgresql://hidden@example/rooms",
        )
        with patch(
            "agentsassemble.application.room_repository_factory._postgres_application_database_type",
            return_value=FakeApplicationDatabase,
        ):
            database = build_postgres_application_database(settings)

        self.assertEqual(database.dsn, settings.postgres_dsn)

    def test_unready_postgres_schema_is_an_explicit_backend_error(self) -> None:
        from agentsassemble.postgres_room_schema import PostgresRoomSchemaNotReady

        settings = RoomRepositorySettings(
            backend="postgresql",
            postgres_dsn="postgresql://hidden@example/rooms",
        )
        with patch(
            "agentsassemble.application.room_repository_factory._postgres_repository_type",
            return_value=object,
        ), patch(
            "agentsassemble.application.room_repository_factory.require_postgres_room_schema",
            side_effect=PostgresRoomSchemaNotReady("authority is not activated"),
        ):
            with self.assertRaisesRegex(RoomRepositoryUnavailable, "not activated") as raised:
                build_room_repository(Path("/tmp/output"), settings)

        self.assertNotIn("hidden", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
