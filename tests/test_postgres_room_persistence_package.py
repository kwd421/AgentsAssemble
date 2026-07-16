from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_PSYCOPG_AVAILABLE = importlib.util.find_spec("psycopg") is not None

if _PSYCOPG_AVAILABLE:
    import agentsassemble.postgres_attention_repository as compatibility_attention
    import agentsassemble.postgres_room_mutations as compatibility_mutations
    import agentsassemble.postgres_room_queries as compatibility_queries
    import agentsassemble.postgres_room_repository as compatibility_repository
    import agentsassemble.postgres_room_rows as compatibility_rows
    import agentsassemble.postgres_room_schema as compatibility_schema
    from agentsassemble.persistence.postgres import schema as owned_schema
    from agentsassemble.persistence.postgres.room import attention as owned_attention
    from agentsassemble.persistence.postgres.room import mutations as owned_mutations
    from agentsassemble.persistence.postgres.room import queries as owned_queries
    from agentsassemble.persistence.postgres.room import repository as owned_repository
    from agentsassemble.persistence.postgres.room import rows as owned_rows


@unittest.skipUnless(_PSYCOPG_AVAILABLE, "the postgres extra is required")
class PostgresRoomPersistencePackageTests(unittest.TestCase):
    def test_root_modules_are_explicit_compatibility_exports(self) -> None:
        self.assertIs(
            compatibility_repository.PostgresRoomRepository,
            owned_repository.PostgresRoomRepository,
        )
        self.assertIs(compatibility_queries.read_room, owned_queries.read_room)
        self.assertIs(compatibility_mutations.create_room, owned_mutations.create_room)
        self.assertIs(compatibility_rows.payload_from_row, owned_rows.payload_from_row)
        self.assertIs(
            compatibility_attention.read_attention_state,
            owned_attention.read_attention_state,
        )
        self.assertIs(
            compatibility_schema.upgrade_postgres_room_schema,
            owned_schema.upgrade_postgres_room_schema,
        )

    def test_migration_scripts_remain_owned_by_the_existing_migration_package(self) -> None:
        self.assertEqual(
            owned_schema._migration_script_path(),
            ROOT / "agentsassemble" / "migrations",
        )


if __name__ == "__main__":
    unittest.main()
