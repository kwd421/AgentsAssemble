from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.persistence.local.identity.repository import IdentityStore
from tests.identity_repository_contract import IdentityRepositoryContractMixin


class SqliteIdentityRepositoryContractTests(
    IdentityRepositoryContractMixin,
    unittest.TestCase,
):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.repository = IdentityStore(
            Path(self._temporary_directory.name) / "identity.db"
        )

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()


if __name__ == "__main__":
    unittest.main()
