from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.persistence.local.identity.repository import IdentityStore
from agentsassemble.room_admission import RoomAdmissionService
from agentsassemble.room_invite import (
    configure_room_invite_store,
    create_room_invite,
    inspect_room_invite,
    join_room_with_invite,
    reset_state,
    verify_session_token,
)
from agentsassemble.room_store import RoomStore
from agentsassemble.application.room_users import device_auth_key


class RoomAdmissionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_state()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(reset_state)
        self.root = Path(self._tmp.name)
        self.identities = IdentityStore(self.root / "identity.db")
        self.rooms = RoomStore(self.root / "rooms")
        self.rooms.create_room("room-a", label="Room A")
        configure_room_invite_store(self.root / "room-invite-state.json")
        self.invite = create_room_invite(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
            display_name="Guest",
        )
        self.service = RoomAdmissionService(
            identities=self.identities,
            rooms=self.rooms,
            invite_inspector=inspect_room_invite,
        )

    def test_unknown_device_requires_profile_without_creating_identity_or_session(self) -> None:
        before_state = (self.root / "room-invite-state.json").read_bytes()

        decision = self.service.resolve(
            invite_token=str(self.invite["join_code"]),
            device_token="unknown-device-token",
        )

        self.assertEqual(decision["status"], "profile_required")
        self.assertFalse(decision["can_auto_join"])
        self.assertEqual(self.identities.count_users(), 0)
        self.assertEqual((self.root / "room-invite-state.json").read_bytes(), before_state)

    def test_known_device_uses_saved_profile_without_join_side_effects(self) -> None:
        device_token = "known-device-token"
        saved = self.identities.resolve_credential_user(
            device_auth_key(device_token),
            display_name="Known Guest",
        )
        before_state = (self.root / "room-invite-state.json").read_bytes()

        decision = self.service.resolve(
            invite_token=str(self.invite["join_code"]),
            device_token=device_token,
        )

        self.assertEqual(decision["status"], "known_user")
        self.assertTrue(decision["can_auto_join"])
        self.assertEqual(decision["participant"]["participant_id"], saved["participant_id"])
        self.assertEqual(decision["participant"]["display_name"], "Known Guest")
        self.assertEqual((self.root / "room-invite-state.json").read_bytes(), before_state)

    def test_existing_canonical_participant_is_reported_as_existing_member(self) -> None:
        device_token = "member-device-token"
        saved = self.identities.resolve_credential_user(
            device_auth_key(device_token),
            display_name="Room Member",
        )
        self.rooms.upsert_participant(
            "room-a",
            {
                "participant_id": saved["participant_id"],
                "display_name": "Current Room Name",
                "participant_type": "human",
                "role": "human",
                "status": "joined",
            },
        )

        decision = self.service.resolve(
            invite_token=str(self.invite["join_code"]),
            device_token=device_token,
        )

        self.assertEqual(decision["status"], "existing_member")
        self.assertEqual(decision["participant"]["display_name"], "Current Room Name")

    def test_matching_valid_session_is_reused_before_device_lookup(self) -> None:
        joined = join_room_with_invite(
            str(self.invite["join_code"]),
            display_name="Already Joined",
        )
        session = verify_session_token(str(joined["session_token"]))
        second_invite = create_room_invite(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
            display_name="Guest",
        )
        before_state = (self.root / "room-invite-state.json").read_bytes()

        decision = self.service.resolve(
            invite_token=str(second_invite["join_code"]),
            session=session,
        )

        self.assertEqual(decision["status"], "existing_session")
        self.assertEqual(decision["participant"]["participant_id"], joined["agent_id"])
        self.assertEqual((self.root / "room-invite-state.json").read_bytes(), before_state)

    def test_invalid_and_expired_invites_are_explicit(self) -> None:
        invalid = self.service.resolve(invite_token="aaj1_missing")
        expired_service = RoomAdmissionService(
            identities=self.identities,
            rooms=self.rooms,
            invite_inspector=lambda _token: {"status": "rejected", "reason": "token_expired"},
        )
        expired = expired_service.resolve(invite_token="expired")

        self.assertEqual(invalid["status"], "invite_invalid")
        self.assertEqual(invalid["reason"], "invite_not_found")
        self.assertEqual(expired["status"], "invite_expired")


class RoomInviteInspectionTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_state()
        self.addCleanup(reset_state)

    def test_inspection_does_not_consume_a_single_use_invite(self) -> None:
        invite = create_room_invite(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
            max_uses=1,
        )

        first = inspect_room_invite(str(invite["invite_token"]))
        second = inspect_room_invite(str(invite["invite_token"]))
        joined = join_room_with_invite(str(invite["invite_token"]))

        self.assertEqual(first["status"], "valid")
        self.assertEqual(second["status"], "valid")
        self.assertEqual(joined["status"], "admitted")

    def test_inspection_never_returns_token_or_nonce_material(self) -> None:
        invite = create_room_invite(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
        )

        inspected = inspect_room_invite(str(invite["join_code"]))
        serialized = repr(inspected)

        self.assertNotIn(str(invite["invite_token"]), serialized)
        self.assertNotIn(str(invite["join_code"]), serialized)
        self.assertNotIn("nonce", serialized)
        self.assertNotIn("fingerprint", serialized)


if __name__ == "__main__":
    unittest.main()
