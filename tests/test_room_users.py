import unittest
from pathlib import Path

from agentsassemble.application import room_users


class RoomUsersStoreLifecycleTests(unittest.TestCase):
    def tearDown(self) -> None:
        room_users.reset_state()

    def test_reset_removes_the_ephemeral_identity_store(self) -> None:
        room_users.reset_state()
        user = room_users.resolve_device_user("device-token-for-ephemeral-store")
        self.assertIsNotNone(user)
        self.assertIsNotNone(room_users._ephemeral_dir)
        directory = Path(room_users._ephemeral_dir.name)
        self.assertTrue(directory.exists())

        room_users.reset_state()

        self.assertFalse(directory.exists())
        self.assertIsNone(room_users._ephemeral_dir)


if __name__ == "__main__":
    unittest.main()
