from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.identity_store import IdentityStore
from agentsassemble.room_admission_coordinator import (
    AdmissionIdempotencyConflict,
    RoomAdmissionCoordinator,
)
from agentsassemble.room_invite import InviteApplicationService
from agentsassemble.room_invite_repository import (
    JsonInviteSessionRepository,
    MemoryInviteSessionRepository,
)
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
            token_key=self.invites.signing_secret,
        )
        self.coordinator = RoomAdmissionCoordinator(
            invites=self.invites,
            sessions=self.sessions,
            identities=self.identities,
            rooms=self.rooms,
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

    def test_same_request_is_admitted_once_and_returns_the_same_session(self) -> None:
        self.rooms.create_room("room-a", label="Room A")
        invite = self.invites.create(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
            display_name="Guest",
            max_uses=2,
        )

        first = self.coordinator.admit(
            invite_token=str(invite["join_code"]),
            request_id="browser-request-1",
            display_name="Known Guest",
            device_token="known-device-token",
        )
        second = self.coordinator.admit(
            invite_token=str(invite["join_code"]),
            request_id="browser-request-1",
            display_name="Known Guest",
            device_token="known-device-token",
        )

        self.assertEqual(second, first)
        self.assertEqual(
            self.repository.invite(str(invite["invite_id"]))["use_count"],
            1,
        )
        self.assertEqual(len(self.repository.list_sessions()), 1)
        self.assertEqual(len(self.rooms.participants("room-a")), 1)

    def test_reusing_request_id_with_changed_payload_is_a_conflict(self) -> None:
        self.rooms.create_room("room-a", label="Room A")
        invite = self.invites.create(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
            display_name="Guest",
            max_uses=2,
        )
        self.coordinator.admit(
            invite_token=str(invite["join_code"]),
            request_id="browser-request-1",
            display_name="First Name",
            device_token="known-device-token",
        )

        with self.assertRaises(AdmissionIdempotencyConflict):
            self.coordinator.admit(
                invite_token=str(invite["join_code"]),
                request_id="browser-request-1",
                display_name="Changed Name",
                device_token="known-device-token",
            )

        self.assertEqual(
            self.repository.invite(str(invite["invite_id"]))["use_count"],
            1,
        )

    def test_failure_after_invite_consumption_resumes_without_reconsuming(self) -> None:
        self.rooms.create_room("room-a", label="Room A")
        invite = self.invites.create(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
            display_name="Guest",
            max_uses=2,
        )
        arguments = {
            "invite_token": str(invite["join_code"]),
            "request_id": "browser-request-1",
            "display_name": "Known Guest",
            "device_token": "known-device-token",
        }

        with patch.object(
            self.identities,
            "upsert_membership",
            side_effect=RuntimeError("identity write failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "identity write failed"):
                self.coordinator.admit(**arguments)

        resumed = self.coordinator.admit(**arguments)

        self.assertEqual(resumed["status"], "admitted")
        self.assertEqual(
            self.repository.invite(str(invite["invite_id"]))["use_count"],
            1,
        )
        self.assertEqual(
            self.identities.get_membership("room-a", str(resumed["agent_id"]))["status"],
            "online",
        )

    def test_incomplete_json_workflow_resumes_after_repository_restart(self) -> None:
        self.rooms.create_room("room-a", label="Room A")
        path = self.root / "invite-state.json"
        repository = JsonInviteSessionRepository(path)
        invites = InviteApplicationService(
            repository,
            public_url=lambda: "https://room.example",
        )
        sessions = RoomSessionService(
            repository,
            token_prefix="aas1",
            ttl_seconds=3600,
            token_key=invites.signing_secret,
        )
        coordinator = RoomAdmissionCoordinator(
            invites=invites,
            sessions=sessions,
            identities=self.identities,
            rooms=self.rooms,
        )
        invite = invites.create(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
            display_name="Guest",
            max_uses=2,
        )
        arguments = {
            "invite_token": str(invite["join_code"]),
            "request_id": "restart-request-1",
            "display_name": "Restart Guest",
            "device_token": "restart-device-secret",
        }
        with patch.object(
            self.identities,
            "upsert_membership",
            side_effect=RuntimeError("identity write failed"),
        ):
            with self.assertRaises(RuntimeError):
                coordinator.admit(**arguments)

        restarted_repository = JsonInviteSessionRepository(path)
        restarted_invites = InviteApplicationService(
            restarted_repository,
            public_url=lambda: "https://room.example",
        )
        restarted_sessions = RoomSessionService(
            restarted_repository,
            token_prefix="aas1",
            ttl_seconds=3600,
            token_key=restarted_invites.signing_secret,
        )
        restarted = RoomAdmissionCoordinator(
            invites=restarted_invites,
            sessions=restarted_sessions,
            identities=self.identities,
            rooms=self.rooms,
        )

        result = restarted.admit(**arguments)
        persisted = path.read_text(encoding="utf-8")

        self.assertEqual(result["status"], "admitted")
        self.assertEqual(
            restarted_repository.invite(str(invite["invite_id"]))["use_count"],
            1,
        )
        self.assertNotIn(str(invite["join_code"]), persisted)
        self.assertNotIn("restart-device-secret", persisted)
        self.assertNotIn(str(result["session_token"]), persisted)
        self.assertIn("admission_workflows", json.loads(persisted))

    def test_completed_workflow_does_not_resurrect_a_replaced_session(self) -> None:
        self.rooms.create_room("room-a", label="Room A")
        invite = self.invites.create(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
            display_name="Guest",
            max_uses=2,
        )
        arguments = {
            "invite_token": str(invite["join_code"]),
            "request_id": "browser-request-1",
            "device_token": "known-device-token",
        }
        admitted = self.coordinator.admit(**arguments)
        self.sessions.revoke(str(admitted["session_token"]))

        retried = self.coordinator.admit(**arguments)

        self.assertEqual(
            retried,
            {"status": "rejected", "reason": "admission_session_unavailable"},
        )

    def test_concurrent_same_request_converges_on_one_admission(self) -> None:
        self.rooms.create_room("room-a", label="Room A")
        invite = self.invites.create(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
            display_name="Guest",
            max_uses=2,
        )
        barrier = threading.Barrier(6)
        results: list[dict[str, object]] = []
        failures: list[BaseException] = []

        def join() -> None:
            try:
                barrier.wait()
                results.append(
                    self.coordinator.admit(
                        invite_token=str(invite["join_code"]),
                        request_id="browser-request-1",
                        device_token="known-device-token",
                    )
                )
            except BaseException as error:
                failures.append(error)

        threads = [threading.Thread(target=join) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertFalse(failures)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(results), 6)
        self.assertEqual({str(result["session_token"]) for result in results}, {str(results[0]["session_token"])})
        self.assertEqual(
            self.repository.invite(str(invite["invite_id"]))["use_count"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
