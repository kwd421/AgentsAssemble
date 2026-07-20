from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agentsassemble.identity_repository_factory as compatibility_factory
from agentsassemble.identity import factory as owned_factory
from agentsassemble.persistence.local.identity.registry import (
    reset_identity_store_registry,
)
from agentsassemble.persistence.local.identity.repository import IdentityStore
from agentsassemble.application.room_repository_factory import (
    RoomRepositoryConfigurationError,
    RoomRepositorySettings,
)


class IdentityRepositoryFactoryTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_identity_store_registry()

    def test_root_module_exports_owned_identity_factory(self) -> None:
        self.assertIs(
            compatibility_factory.build_identity_repository,
            owned_factory.build_identity_repository,
        )

    def test_sqlite_uses_the_output_root_identity_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = owned_factory.build_identity_repository(
                root,
                RoomRepositorySettings(backend="sqlite"),
            )

            self.assertIsInstance(repository, IdentityStore)
            self.assertEqual(repository.db_path, root / "identity.db")

    def test_postgres_requires_the_selected_dsn_without_local_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = RoomRepositorySettings(
                backend="postgresql",
                postgres_dsn_env="ROOM_DATABASE_URL",
            )

            with self.assertRaisesRegex(
                RoomRepositoryConfigurationError,
                "ROOM_DATABASE_URL",
            ):
                owned_factory.build_identity_repository(root, settings)

            self.assertFalse((root / "identity.db").exists())

    def test_postgres_factory_receives_dsn_without_local_state(self) -> None:
        class FakePostgresRepository:
            def __init__(self, dsn: str) -> None:
                self.dsn = dsn

        settings = RoomRepositorySettings(
            backend="postgresql",
            postgres_dsn="postgresql://user:secret@example.invalid/rooms",
        )
        with patch(
            "agentsassemble.identity.factory._postgres_repository_type",
            return_value=FakePostgresRepository,
        ):
            repository = owned_factory.build_identity_repository(
                Path("/tmp/unused"),
                settings,
            )

        self.assertEqual(repository.dsn, settings.postgres_dsn)

    def test_postgres_factory_injects_application_database_without_a_second_dsn_owner(self) -> None:
        database = object()

        class FakePostgresRepository:
            def __init__(self, *, database) -> None:
                self.database = database

        settings = RoomRepositorySettings(
            backend="postgresql",
            postgres_dsn="postgresql://user:secret@example.invalid/rooms",
        )
        with patch(
            "agentsassemble.identity.factory._postgres_repository_type",
            return_value=FakePostgresRepository,
        ):
            repository = owned_factory.build_identity_repository(
                Path("/tmp/unused"),
                settings,
                postgres_database=database,
            )

        self.assertIs(repository.database, database)


if __name__ == "__main__":
    unittest.main()
