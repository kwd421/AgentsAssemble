from __future__ import annotations

import importlib.util
import unittest


_PSYCOPG_AVAILABLE = importlib.util.find_spec("psycopg") is not None

if _PSYCOPG_AVAILABLE:
    import agentsassemble.postgres_invite_repository as compatibility_repository
    from agentsassemble.persistence.postgres.admission import (
        repository as owned_repository,
    )


@unittest.skipUnless(_PSYCOPG_AVAILABLE, "the postgres extra is required")
class PostgresAdmissionPersistencePackageTests(unittest.TestCase):
    def test_root_module_is_an_explicit_compatibility_export(self) -> None:
        self.assertIs(
            compatibility_repository.PostgresInviteSessionRepository,
            owned_repository.PostgresInviteSessionRepository,
        )


if __name__ == "__main__":
    unittest.main()
