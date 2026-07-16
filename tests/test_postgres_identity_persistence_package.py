from __future__ import annotations

import importlib.util
import unittest


_PSYCOPG_AVAILABLE = importlib.util.find_spec("psycopg") is not None

if _PSYCOPG_AVAILABLE:
    import agentsassemble.postgres_identity_preferences as compatibility_preferences
    import agentsassemble.postgres_identity_repository as compatibility_repository
    import agentsassemble.postgres_identity_roster as compatibility_roster
    import agentsassemble.postgres_identity_usage as compatibility_usage
    import agentsassemble.postgres_identity_users as compatibility_users
    from agentsassemble.persistence.postgres.identity import (
        preferences as owned_preferences,
    )
    from agentsassemble.persistence.postgres.identity import (
        repository as owned_repository,
    )
    from agentsassemble.persistence.postgres.identity import roster as owned_roster
    from agentsassemble.persistence.postgres.identity import usage as owned_usage
    from agentsassemble.persistence.postgres.identity import users as owned_users


@unittest.skipUnless(_PSYCOPG_AVAILABLE, "the postgres extra is required")
class PostgresIdentityPersistencePackageTests(unittest.TestCase):
    def test_root_modules_are_explicit_compatibility_exports(self) -> None:
        self.assertIs(
            compatibility_repository.PostgresIdentityRepository,
            owned_repository.PostgresIdentityRepository,
        )
        self.assertIs(
            compatibility_preferences.read_room_preferences,
            owned_preferences.read_room_preferences,
        )
        self.assertIs(
            compatibility_roster.list_memberships,
            owned_roster.list_memberships,
        )
        self.assertIs(compatibility_usage.record_usage, owned_usage.record_usage)
        self.assertIs(
            compatibility_users.resolve_credential_user,
            owned_users.resolve_credential_user,
        )


if __name__ == "__main__":
    unittest.main()
