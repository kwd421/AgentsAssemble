from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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

if __name__ == "__main__":
    unittest.main()
