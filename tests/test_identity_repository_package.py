from __future__ import annotations

import unittest

import agentsassemble.identity_store as compatibility_store
from agentsassemble.identity import repository as owned_repository


class IdentityRepositoryPackageTests(unittest.TestCase):
    def test_root_module_reexports_owned_identity_contract(self) -> None:
        self.assertIs(
            compatibility_store.IdentityBackend,
            owned_repository.IdentityBackend,
        )
        self.assertIs(
            compatibility_store.device_auth_key,
            owned_repository.device_auth_key,
        )
        self.assertIs(
            compatibility_store.normalize_participant_type,
            owned_repository.normalize_participant_type,
        )
        self.assertEqual(
            compatibility_store.LOCAL_OPERATOR_PARTICIPANT_ID,
            owned_repository.LOCAL_OPERATOR_PARTICIPANT_ID,
        )
        self.assertEqual(
            compatibility_store.LOCAL_OPERATOR_USER_ID,
            owned_repository.LOCAL_OPERATOR_USER_ID,
        )


if __name__ == "__main__":
    unittest.main()
