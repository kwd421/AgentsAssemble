from __future__ import annotations

import importlib
import sqlite3
import sys
import types
import unittest


class _SqliteMigrationOperations:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def execute(self, statement: str) -> None:
        self._connection.execute(statement)


class MembershipScopeMigrationTests(unittest.TestCase):
    def _run_upgrade(self, module_name: str, connection: sqlite3.Connection) -> None:
        sys.modules.pop(module_name, None)
        fake_alembic = types.SimpleNamespace(
            op=_SqliteMigrationOperations(connection)
        )
        previous_alembic = sys.modules.get("alembic")
        try:
            sys.modules["alembic"] = fake_alembic
            migration = importlib.import_module(module_name)
            migration.upgrade()
        finally:
            sys.modules.pop(module_name, None)
            if previous_alembic is None:
                sys.modules.pop("alembic", None)
            else:
                sys.modules["alembic"] = previous_alembic

    def test_initial_scope_migration_preserves_only_historical_room_authority(self) -> None:
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.executescript(
            """
            CREATE TABLE identity_memberships (
                meeting_id TEXT NOT NULL,
                participant_id TEXT NOT NULL
            );
            CREATE TABLE room_access_sessions (
                room_id TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                invite_scope TEXT NOT NULL
            );
            INSERT INTO identity_memberships VALUES
                ('room-a', 'historical-writer'),
                ('room-a', 'historical-reader'),
                ('room-a', 'no-session');
            INSERT INTO room_access_sessions VALUES
                ('room-a', 'historical-writer', 'room'),
                ('room-a', 'historical-reader', 'read_only');
            """
        )
        self._run_upgrade(
            "agentsassemble.migrations.versions.0019_membership_invite_scope",
            connection,
        )

        scopes = dict(
            connection.execute(
                "SELECT participant_id, invite_scope FROM identity_memberships"
            )
        )
        self.assertEqual(
            scopes,
            {
                "historical-writer": "room",
                "historical-reader": "read_only",
                "no-session": "read_only",
            },
        )

    def test_audit_migration_downgrades_memberships_without_write_evidence(self) -> None:
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.executescript(
            """
            CREATE TABLE identity_memberships (
                meeting_id TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                invite_scope TEXT NOT NULL
            );
            CREATE TABLE room_access_sessions (
                room_id TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                invite_scope TEXT NOT NULL
            );
            INSERT INTO identity_memberships VALUES
                ('room-a', 'historical-writer', 'room'),
                ('room-a', 'historical-reader', 'room'),
                ('room-a', 'no-session', 'room');
            INSERT INTO room_access_sessions VALUES
                ('room-a', 'historical-writer', 'room'),
                ('room-a', 'historical-reader', 'read_only');
            """
        )
        self._run_upgrade(
            "agentsassemble.migrations.versions.0021_membership_scope_audit",
            connection,
        )

        scopes = dict(
            connection.execute(
                "SELECT participant_id, invite_scope FROM identity_memberships"
            )
        )
        self.assertEqual(
            scopes,
            {
                "historical-writer": "room",
                "historical-reader": "read_only",
                "no-session": "read_only",
            },
        )


if __name__ == "__main__":
    unittest.main()
