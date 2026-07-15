from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.identity_store import IdentityStore
from agentsassemble.room_admission_coordinator import RoomAdmissionCoordinator
from agentsassemble.room_invite import InviteApplicationService
from agentsassemble.room_invite_repository import MemoryInviteSessionRepository
from agentsassemble.room_session_service import RoomSessionService
from agentsassemble.room_store import RoomStore


class RoomAdmissionCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.rooms = RoomStore(self.root)
        self.addCleanup(self.rooms.close)
        self.identities = IdentityStore(self.root / "identity.db")
        self.repository = MemoryInviteSessionRepository()
        self.invites = InviteApplicationService(
            self.repository,
            public_url=lambda: "https://room.example",
        )
        self.sessions = RoomSessionService(
            self.repository,
            token_prefix="aas1",
            ttl_seconds=3600,
            token_factory=lambda: "session-token",
        )
        self.coordinator = RoomAdmissionCoordinator(
            invites=self.invites,
            sessions=self.sessions,
            identities=self.identities,
            rooms=self.rooms,
            participant_suffix=lambda: "suffix",
        )

    def test_admission_commits_session_participant_and_membership(self) -> None:
        self.rooms.create_room("room-a", label="Room A")
        invite = self.invites.create(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
            display_name="Guest",
            max_uses=0,
        )

        result = self.coordinator.admit(
            invite_token=str(invite["join_code"]),
            display_name="Known Guest",
            device_token="known-device-token",
        )

        self.assertEqual(result["status"], "admitted")
        self.assertEqual(result["room_label"], "Room A")
        self.assertTrue(result["stable_identity"])
        participant_id = str(result["agent_id"])
        self.assertEqual(
            self.rooms.participant("room-a", participant_id)["display_name"],
            "Known Guest",
        )
        self.assertEqual(
            self.identities.get_membership("room-a", participant_id)["status"],
            "online",
        )
        self.assertEqual(
            self.sessions.verify(str(result["session_token"]))["agent_id"],
            participant_id,
        )

    def test_missing_room_does_not_consume_invite(self) -> None:
        invite = self.invites.create(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-later",
            display_name="Guest",
            max_uses=1,
        )

        rejected = self.coordinator.admit(invite_token=str(invite["join_code"]))
        self.rooms.create_room("room-later", label="Later")
        admitted = self.coordinator.admit(invite_token=str(invite["join_code"]))

        self.assertEqual(rejected, {"status": "rejected", "reason": "room_unavailable"})
        self.assertEqual(admitted["status"], "admitted")

    def test_single_use_invite_cannot_create_a_second_membership(self) -> None:
        self.rooms.create_room("room-a", label="Room A")
        invite = self.invites.create(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
            agent_id="single-guest",
            max_uses=1,
        )

        first = self.coordinator.admit(invite_token=str(invite["join_code"]))
        second = self.coordinator.admit(invite_token=str(invite["join_code"]))

        self.assertEqual(first["status"], "admitted")
        self.assertEqual(second, {"status": "rejected", "reason": "token_already_used"})
        self.assertEqual(len(self.rooms.participants("room-a")), 1)


if __name__ == "__main__":
    unittest.main()
