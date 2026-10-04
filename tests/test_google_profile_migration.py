from __future__ import annotations

import importlib
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path

from agentsassemble.persistence.local.identity.repository import IdentityStore


class GoogleProfileMigrationTests(unittest.TestCase):
    def test_reopening_local_identity_store_removes_legacy_google_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "identity.db"
            original = IdentityStore(database_path)
            user = original.resolve_credential_user(
                "device:legacy-google-profile",
                user_id="legacy-google-user",
                participant_id="legacy-google-participant",
            )
            original.connect_external_account(
                str(user["user_id"]),
                account_id="acct-legacy-google",
                provider="google",
                subject_fingerprint="legacy-google-subject",
                display_name="Legacy Google Name",
                email="legacy@example.invalid",
                avatar_image_url="https://google.invalid/legacy-avatar",
                connected_at="2026-08-20T00:00:00+00:00",
            )

            reopened = IdentityStore(database_path)
            account = reopened.external_account_for_user(str(user["user_id"]))

        self.assertEqual(account["display_name"], "")
        self.assertEqual(account["email"], "")
        self.assertEqual(account["avatar_image_url"], "")

    def test_upgrade_removes_only_google_profile_claims(self) -> None:
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.executescript(
            """
            CREATE TABLE identity_accounts (
                account_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                email TEXT NOT NULL,
                avatar_image_url TEXT NOT NULL
            );
            CREATE TABLE identity_external_accounts (
                provider TEXT NOT NULL,
                account_id TEXT NOT NULL
            );
            INSERT INTO identity_accounts VALUES
                ('google-account', 'Google Name', 'google@example.invalid', 'https://google.invalid/avatar'),
                ('other-account', 'Other Name', 'other@example.invalid', 'https://other.invalid/avatar');
            INSERT INTO identity_external_accounts VALUES
                ('google', 'google-account'),
                ('other', 'other-account');
            """
        )
        fake_alembic = types.SimpleNamespace(
            op=types.SimpleNamespace(execute=connection.execute)
        )
        previous_alembic = sys.modules.get("alembic")
        try:
            sys.modules["alembic"] = fake_alembic
            migration = importlib.import_module(
                "agentsassemble.migrations.versions.0022_google_profile_minimization"
            )
            migration.upgrade()
        finally:
            if previous_alembic is None:
                sys.modules.pop("alembic", None)
            else:
                sys.modules["alembic"] = previous_alembic

        rows = {
            row[0]: row[1:]
            for row in connection.execute(
                """SELECT account_id, display_name, email, avatar_image_url
                   FROM identity_accounts ORDER BY account_id"""
            )
        }
        self.assertEqual(rows["google-account"], ("", "", ""))
        self.assertEqual(
            rows["other-account"],
            (
                "Other Name",
                "other@example.invalid",
                "https://other.invalid/avatar",
            ),
        )


if __name__ == "__main__":
    unittest.main()
