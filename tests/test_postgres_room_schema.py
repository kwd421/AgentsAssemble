from __future__ import annotations

import importlib.util
import unittest
from unittest.mock import patch

from agentsassemble.postgres_room_schema import (
    POSTGRES_ROOM_SCHEMA_REVISION,
    PostgresRoomMigrationError,
    _sqlalchemy_psycopg_url,
    upgrade_postgres_room_schema,
)


class PostgresRoomSchemaTests(unittest.TestCase):
    def test_current_revision_requires_authority_activation(self) -> None:
        self.assertEqual(
            POSTGRES_ROOM_SCHEMA_REVISION,
            "0014_guest_recovery_codes",
        )

    def test_sqlalchemy_url_explicitly_selects_psycopg3(self) -> None:
        self.assertEqual(
            _sqlalchemy_psycopg_url("postgresql://user:secret@localhost/rooms"),
            "postgresql+psycopg://user:secret@localhost/rooms",
        )
        self.assertEqual(
            _sqlalchemy_psycopg_url("postgres://localhost/rooms"),
            "postgresql+psycopg://localhost/rooms",
        )

    def test_invalid_dsn_scheme_is_rejected(self) -> None:
        with self.assertRaisesRegex(PostgresRoomMigrationError, "must use"):
            _sqlalchemy_psycopg_url("sqlite:///rooms.db")

    @unittest.skipUnless(importlib.util.find_spec("sqlalchemy"), "postgres extra is not installed")
    def test_migration_error_does_not_include_dsn_or_driver_message(self) -> None:
        class FailingEngine:
            def connect(self):
                raise RuntimeError("could not connect with password secret-value")

            def dispose(self):
                return None

        with patch("sqlalchemy.create_engine", return_value=FailingEngine()):
            with self.assertRaises(PostgresRoomMigrationError) as raised:
                upgrade_postgres_room_schema("postgresql://user:secret-value@example/rooms")

        self.assertNotIn("secret-value", str(raised.exception))
        self.assertEqual(
            str(raised.exception),
            "PostgreSQL room schema migration failed: RuntimeError.",
        )


if __name__ == "__main__":
    unittest.main()
