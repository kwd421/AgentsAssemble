from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.room_invite_repository import JsonInviteSessionRepository
from agentsassemble.room_invite_repository_factory import (
    build_invite_session_repository,
)
from agentsassemble.room_repository_factory import (
    RoomRepositoryConfigurationError,
    RoomRepositorySettings,
)


class RoomInviteRepositoryFactoryTests(unittest.TestCase):
    def test_sqlite_uses_the_existing_local_json_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = build_invite_session_repository(
                root,
                RoomRepositorySettings(backend="sqlite"),
            )

            self.assertIsInstance(repository, JsonInviteSessionRepository)
            self.assertEqual(
                repository.path,
                root / ".agentsassemble" / "room-invite-state.json",
            )
            self.assertFalse(repository.path.exists())

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
                build_invite_session_repository(root, settings)

            self.assertFalse((root / ".agentsassemble" / "room-invite-state.json").exists())

    def test_postgres_factory_receives_dsn_without_putting_it_in_local_state(self) -> None:
        class FakePostgresRepository:
            def __init__(self, dsn: str) -> None:
                self.dsn = dsn

        settings = RoomRepositorySettings(
            backend="postgresql",
            postgres_dsn="postgresql://user:secret@example.invalid/rooms",
        )
        with patch(
            "agentsassemble.room_invite_repository_factory._postgres_repository_type",
            return_value=FakePostgresRepository,
        ):
            repository = build_invite_session_repository(Path("/tmp/unused"), settings)

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
            "agentsassemble.room_invite_repository_factory._postgres_repository_type",
            return_value=FakePostgresRepository,
        ):
            repository = build_invite_session_repository(
                Path("/tmp/unused"),
                settings,
                postgres_database=database,
            )

        self.assertIs(repository.database, database)


if __name__ == "__main__":
    unittest.main()
