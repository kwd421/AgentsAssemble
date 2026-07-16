from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.room.repository import RoomRepository
from agentsassemble.room_store import RoomStore
from tests.room_repository_contract import RoomRepositoryContractMixin


class SQLiteRoomRepositoryContractTests(RoomRepositoryContractMixin, unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.repository = RoomStore(Path(self._temporary_directory.name))

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_room_store_implements_repository_protocol(self) -> None:
        self.assertIsInstance(self.repository, RoomRepository)


if __name__ == "__main__":
    unittest.main()
