from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import agentsassemble.identity_store as compatibility_store
from agentsassemble.persistence.local.identity import migration as owned_migration
from agentsassemble.persistence.local.identity import registry as owned_registry
from agentsassemble.persistence.local.identity import repository as owned_repository


class LocalIdentityPersistencePackageTests(unittest.TestCase):
    def tearDown(self) -> None:
        owned_registry.reset_identity_store_registry()

    def test_root_module_exports_owned_local_identity_boundaries(self) -> None:
        self.assertIs(
            compatibility_store.IdentityStore,
            owned_repository.IdentityStore,
        )
        self.assertIs(
            compatibility_store.identity_store_for_output_root,
            owned_registry.identity_store_for_output_root,
        )
        self.assertIs(
            compatibility_store.migrate_legacy_users_json,
            owned_migration.migrate_legacy_users_json,
        )

    def test_compatibility_and_owned_paths_share_one_sqlite_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            compatibility = compatibility_store.identity_store_for_output_root(root)
            compatibility.resolve_credential_user(
                "device:package-test",
                display_name="Package Test",
            )

            owned = owned_registry.identity_store_for_output_root(root)

            self.assertIs(owned, compatibility)
            self.assertEqual(
                owned.user_for_credential("device:package-test")["display_name"],
                "Package Test",
            )


if __name__ == "__main__":
    unittest.main()
