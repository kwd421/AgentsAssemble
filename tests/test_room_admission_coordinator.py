from __future__ import annotations

import json
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from agentsassemble.identity_store import IdentityStore, device_auth_key
from agentsassemble.room_admission_coordinator import (
    AdmissionIdempotencyConflict,
    RoomAdmissionCoordinator,
)
from agentsassemble.room_invite_application import InviteApplicationService
from agentsassemble.room_invite_repository import (
    JsonInviteSessionRepository,
    MemoryInviteSessionRepository,
)
from agentsassemble.room_session_service import RoomSessionService
from agentsassemble.room_store import RoomStore


class _RecordingTransactionBoundary:
    def __init__(self) -> None:
        self.active = False
        self.events: list[str] = []

    @contextmanager
    def transaction(self):
        self.events.append("begin")
        self.active = True
        try:
            yield object()
        except BaseException:
            self.events.append("rollback")
            raise
        else:
            self.events.append("commit")
        finally:
            self.active = False


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

    def test_each_admission_phase_failure_converges_without_duplicate_state(self) -> None:
        cases = (
            "identity_created",
            "invite_consumed",
            "session_saved",
            "participant_upserted",
            "membership_upserted",
        )

        for index, phase in enumerate(cases, start=1):
            with self.subTest(phase=phase):
                room_id = f"phase-room-{index}"
                request_id = f"phase-request-{index}"
                device_token = f"phase-device-{index}"
                self.rooms.create_room(room_id, label=f"Phase Room {index}")
                invite = self.invites.create(
                    room_url="http://127.0.0.1:8765",
                    meeting_id=room_id,
                    display_name="Phase Guest",
                    max_uses=2,
                )
                arguments = {
                    "invite_token": str(invite["join_code"]),
                    "request_id": request_id,
                    "display_name": "Phase Guest",
                    "device_token": device_token,
                }

                with self._fail_admission_once(phase):
                    with self.assertRaisesRegex(RuntimeError, f"{phase} failure"):
                        self.coordinator.admit(**arguments)

                result = self.coordinator.admit(**arguments)
                participant_id = str(result["agent_id"])
                stable_user = self.identities.user_for_credential(
                    device_auth_key(device_token)
                )
                room_sessions = [
                    session
                    for _, session in self.repository.list_sessions()
                    if session.get("meeting_id") == room_id
                ]

                self.assertEqual(result["status"], "admitted")
                self.assertFalse(result["operator"])
                self.assertIsNotNone(stable_user)
                self.assertFalse(stable_user["is_operator"])
                self.assertEqual(
                    self.repository.invite(str(invite["invite_id"]))["use_count"],
                    1,
                )
                self.assertEqual(
                    [row["participant_id"] for row in self.rooms.participants(room_id)],
                    [participant_id],
                )
                self.assertEqual(
                    [
                        row["participant_id"]
                        for row in self.identities.list_memberships(room_id)
                    ],
                    [participant_id],
                )
                self.assertEqual(len(room_sessions), 1)
                self.assertEqual(room_sessions[0]["agent_id"], participant_id)

    def test_failed_atomic_session_replacement_preserves_old_session_until_retry(self) -> None:
        self.rooms.create_room("room-a", label="Room A")
        device_token = "replacement-device"
        stable_user = self.identities.resolve_credential_user(
            device_auth_key(device_token),
            provider="device",
            display_name="Replacement Guest",
            participant_type="human",
        )
        participant_id = str(stable_user["participant_id"])
        old_token, old_session = self.sessions.issue(
            {
                "agent_id": participant_id,
                "display_name": "Replacement Guest",
                "meeting_id": "room-a",
                "participant_type": "human",
                "client_type": "browser",
            }
        )
        invite = self.invites.create(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
            display_name="Replacement Guest",
            max_uses=2,
        )
        arguments = {
            "invite_token": str(invite["join_code"]),
            "request_id": "atomic-session-replacement",
            "display_name": "Replacement Guest",
            "device_token": device_token,
        }

        with patch.object(
            self.repository,
            "replace_participant_session",
            side_effect=RuntimeError("atomic replacement failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "atomic replacement failure"):
                self.coordinator.admit(**arguments)

        self.assertEqual(self.sessions.verify(old_token), old_session)
        self.assertEqual(len(self.repository.list_sessions()), 1)

        result = self.coordinator.admit(**arguments)

        self.assertEqual(result["status"], "admitted")
        self.assertIsNone(self.sessions.verify(old_token))
        self.assertEqual(len(self.repository.list_sessions()), 1)
        self.assertEqual(
            self.sessions.verify(str(result["session_token"]))["agent_id"],
            participant_id,
        )

    def test_deleted_room_compensates_partial_local_admission(self) -> None:
        invite, arguments, workflow_id, session_token, participant_id = (
            self._leave_partial_admission(request_id="deleted-room-compensation")
        )
        self.assertIsNotNone(self.sessions.verify(session_token))
        self.assertIsNotNone(self.identities.get_membership("room-a", participant_id))
        self.assertTrue(self.rooms.delete_room("room-a"))

        rejected = self.coordinator.admit(**arguments)
        repeated = self.coordinator.admit(**arguments)
        workflow = self.repository.admission_workflow(workflow_id)

        self.assertEqual(rejected, {"status": "rejected", "reason": "room_unavailable"})
        self.assertEqual(repeated, rejected)
        self.assertIsNone(self.sessions.verify(session_token))
        self.assertIsNone(self.identities.get_membership("room-a", participant_id))
        self.assertEqual(workflow["status"], "failed_terminal")
        self.assertEqual(workflow["resume_phase"], "compensated")
        self.assertEqual(workflow["compensation_status"], "completed")
        self.assertTrue(workflow["session_compensated"])
        self.assertTrue(workflow["membership_compensated"])
        self.assertTrue(workflow["invite_consumption_retained"])
        self.assertEqual(
            self.repository.invite(str(invite["invite_id"]))["use_count"],
            1,
        )

    def test_failed_local_compensation_resumes_at_remaining_side_effect(self) -> None:
        _, arguments, workflow_id, session_token, participant_id = (
            self._leave_partial_admission(request_id="retry-compensation")
        )
        self.assertTrue(self.rooms.delete_room("room-a"))
        remove_membership = self.identities.remove_membership
        attempts = 0

        def fail_first_membership_removal(room_id: str, member_id: str) -> bool:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("membership compensation failed")
            return remove_membership(room_id, member_id)

        with patch.object(
            self.identities,
            "remove_membership",
            side_effect=fail_first_membership_removal,
        ):
            with self.assertRaisesRegex(RuntimeError, "membership compensation failed"):
                self.coordinator.admit(**arguments)

        failed = self.repository.admission_workflow(workflow_id)
        self.assertEqual(failed["status"], "failed_retryable")
        self.assertEqual(failed["resume_phase"], "compensating")
        self.assertEqual(failed["compensation_status"], "failed_retryable")
        self.assertEqual(failed["compensation_failure_code"], "RuntimeError")
        self.assertTrue(failed["session_compensated"])
        self.assertFalse(failed["membership_compensated"])
        self.assertIsNone(self.sessions.verify(session_token))
        self.assertIsNotNone(self.identities.get_membership("room-a", participant_id))

        rejected = self.coordinator.admit(**arguments)
        completed = self.repository.admission_workflow(workflow_id)

        self.assertEqual(rejected, {"status": "rejected", "reason": "room_unavailable"})
        self.assertEqual(completed["status"], "failed_terminal")
        self.assertEqual(completed["compensation_status"], "completed")
        self.assertTrue(completed["session_compensated"])
        self.assertTrue(completed["membership_compensated"])
        self.assertIsNone(self.identities.get_membership("room-a", participant_id))

    def test_failed_compensation_completion_record_is_retryable(self) -> None:
        _, arguments, workflow_id, session_token, participant_id = (
            self._leave_partial_admission(request_id="retry-compensation-record")
        )
        self.assertTrue(self.rooms.delete_room("room-a"))
        update_workflow = self.repository.update_admission_workflow

        def fail_terminal_record(workflow: str, updates: dict[str, object]):
            if updates.get("status") == "failed_terminal":
                raise RuntimeError("compensation completion write failed")
            return update_workflow(workflow, updates)

        with patch.object(
            self.repository,
            "update_admission_workflow",
            side_effect=fail_terminal_record,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "compensation completion write failed",
            ):
                self.coordinator.admit(**arguments)

        failed = self.repository.admission_workflow(workflow_id)
        self.assertEqual(failed["status"], "failed_retryable")
        self.assertEqual(failed["compensation_status"], "failed_retryable")
        self.assertTrue(failed["session_compensated"])
        self.assertTrue(failed["membership_compensated"])
        self.assertIsNone(self.sessions.verify(session_token))
        self.assertIsNone(self.identities.get_membership("room-a", participant_id))

        rejected = self.coordinator.admit(**arguments)

        self.assertEqual(rejected, {"status": "rejected", "reason": "room_unavailable"})
        self.assertEqual(
            self.repository.admission_workflow(workflow_id)["status"],
            "failed_terminal",
        )

    def test_hosted_boundary_surrounds_cross_authority_success_writes(self) -> None:
        self.rooms.create_room("room-a", label="Room A")
        invite = self.invites.create(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
            display_name="Guest",
            max_uses=2,
        )
        boundary = _RecordingTransactionBoundary()
        coordinator = RoomAdmissionCoordinator(
            invites=self.invites,
            sessions=self.sessions,
            identities=self.identities,
            rooms=self.rooms,
            transaction_boundary=boundary,
        )
        consume = self.repository.consume_for_admission
        upsert_membership = self.identities.upsert_membership

        def consume_inside(*args, **kwargs):
            self.assertTrue(boundary.active)
            return consume(*args, **kwargs)

        def membership_inside(record):
            self.assertTrue(boundary.active)
            return upsert_membership(record)

        with patch.object(
            self.repository,
            "consume_for_admission",
            side_effect=consume_inside,
        ), patch.object(
            self.identities,
            "upsert_membership",
            side_effect=membership_inside,
        ):
            result = coordinator.admit(
                invite_token=str(invite["join_code"]),
                request_id="hosted-success",
                device_token="known-device-token",
            )

        self.assertEqual(result["status"], "admitted")
        self.assertEqual(boundary.events, ["begin", "commit"])

    def test_hosted_boundary_rolls_back_before_retryable_failure_is_recorded(self) -> None:
        self.rooms.create_room("room-a", label="Room A")
        invite = self.invites.create(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
            display_name="Guest",
            max_uses=2,
        )
        boundary = _RecordingTransactionBoundary()
        coordinator = RoomAdmissionCoordinator(
            invites=self.invites,
            sessions=self.sessions,
            identities=self.identities,
            rooms=self.rooms,
            transaction_boundary=boundary,
        )
        update_workflow = self.repository.update_admission_workflow

        def track_failure_status(workflow_id, updates):
            if updates.get("status") == "failed_retryable":
                self.assertFalse(boundary.active)
            return update_workflow(workflow_id, updates)

        with patch.object(
            self.identities,
            "upsert_membership",
            side_effect=RuntimeError("identity write failed"),
        ), patch.object(
            self.repository,
            "update_admission_workflow",
            side_effect=track_failure_status,
        ):
            with self.assertRaisesRegex(RuntimeError, "identity write failed"):
                coordinator.admit(
                    invite_token=str(invite["join_code"]),
                    request_id="hosted-failure",
                    device_token="known-device-token",
                )

        self.assertEqual(boundary.events, ["begin", "rollback"])

    def test_incomplete_json_workflow_resumes_after_repository_restart(self) -> None:
        self.rooms.create_room("room-a", label="Room A")
        path = self.root / "invite-state.json"
        repository, invites, _, coordinator = self._json_admission_runtime(path)
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

        restarted_repository, _, _, restarted = self._json_admission_runtime(path)

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

    def test_json_compensation_resumes_after_repository_restart(self) -> None:
        self.rooms.create_room("room-a", label="Room A")
        path = self.root / "invite-state.json"
        repository, invites, sessions, coordinator = self._json_admission_runtime(path)
        invite = invites.create(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
            display_name="Guest",
            max_uses=2,
        )
        arguments = {
            "invite_token": str(invite["join_code"]),
            "request_id": "restart-compensation",
            "display_name": "Restart Compensation Guest",
            "device_token": "restart-compensation-device",
        }
        update_workflow = repository.update_admission_workflow
        workflow_ids: list[str] = []

        def fail_completion(workflow_id: str, updates: dict[str, object]):
            if updates.get("status") == "completed":
                workflow_ids.append(workflow_id)
                raise RuntimeError("completion write failed")
            return update_workflow(workflow_id, updates)

        with patch.object(
            repository,
            "update_admission_workflow",
            side_effect=fail_completion,
        ):
            with self.assertRaisesRegex(RuntimeError, "completion write failed"):
                coordinator.admit(**arguments)

        workflow_id = workflow_ids[0]
        session_token = sessions.token_for_request(workflow_id)
        session = sessions.verify(session_token)
        participant_id = str(session["agent_id"])
        self.assertTrue(self.rooms.delete_room("room-a"))
        with patch.object(
            self.identities,
            "remove_membership",
            side_effect=RuntimeError("membership compensation failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "membership compensation failed"):
                coordinator.admit(**arguments)

        persisted_failure = repository.admission_workflow(workflow_id)
        self.assertEqual(persisted_failure["compensation_status"], "failed_retryable")
        self.assertTrue(persisted_failure["session_compensated"])
        self.assertFalse(persisted_failure["membership_compensated"])

        restarted_repository, _, restarted_sessions, restarted = (
            self._json_admission_runtime(path)
        )
        rejected = restarted.admit(**arguments)
        completed = restarted_repository.admission_workflow(workflow_id)

        self.assertEqual(rejected, {"status": "rejected", "reason": "room_unavailable"})
        self.assertEqual(completed["status"], "failed_terminal")
        self.assertEqual(completed["compensation_status"], "completed")
        self.assertIsNone(restarted_sessions.verify(session_token))
        self.assertIsNone(self.identities.get_membership("room-a", participant_id))
        persisted = path.read_text(encoding="utf-8")
        self.assertNotIn(str(invite["join_code"]), persisted)
        self.assertNotIn("restart-compensation-device", persisted)
        self.assertNotIn(session_token, persisted)

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

    def _leave_partial_admission(
        self,
        *,
        request_id: str,
    ) -> tuple[dict[str, object], dict[str, str], str, str, str]:
        self.rooms.create_room("room-a", label="Room A")
        invite = self.invites.create(
            room_url="http://127.0.0.1:8765",
            meeting_id="room-a",
            display_name="Guest",
            max_uses=2,
        )
        arguments = {
            "invite_token": str(invite["join_code"]),
            "request_id": request_id,
            "display_name": "Partial Guest",
            "device_token": "partial-device-token",
        }
        update_workflow = self.repository.update_admission_workflow
        workflow_ids: list[str] = []

        def fail_completion(workflow_id: str, updates: dict[str, object]):
            if updates.get("status") == "completed":
                workflow_ids.append(workflow_id)
                raise RuntimeError("completion write failed")
            return update_workflow(workflow_id, updates)

        with patch.object(
            self.repository,
            "update_admission_workflow",
            side_effect=fail_completion,
        ):
            with self.assertRaisesRegex(RuntimeError, "completion write failed"):
                self.coordinator.admit(**arguments)

        workflow_id = workflow_ids[0]
        session_token = self.sessions.token_for_request(workflow_id)
        session = self.sessions.verify(session_token)
        self.assertIsNotNone(session)
        return (
            invite,
            arguments,
            workflow_id,
            session_token,
            str(session["agent_id"]),
        )

    @contextmanager
    def _fail_admission_once(self, phase: str):
        if phase == "invite_consumed":
            with patch.object(
                self.sessions,
                "ensure_for_request",
                side_effect=RuntimeError(f"{phase} failure"),
            ):
                yield
            return
        if phase == "participant_upserted":
            with patch.object(
                self.identities,
                "upsert_membership",
                side_effect=RuntimeError(f"{phase} failure"),
            ):
                yield
            return

        target_status = {
            "identity_created": "identity_resolved",
            "session_saved": "session_issued",
            "membership_upserted": "membership_committed",
        }[phase]
        update = self.repository.update_admission_workflow

        def fail_phase_update(workflow_id: str, updates: dict[str, object]):
            if updates.get("status") == target_status:
                raise RuntimeError(f"{phase} failure")
            return update(workflow_id, updates)

        with patch.object(
            self.repository,
            "update_admission_workflow",
            side_effect=fail_phase_update,
        ):
            yield

    def _json_admission_runtime(
        self,
        path: Path,
    ) -> tuple[
        JsonInviteSessionRepository,
        InviteApplicationService,
        RoomSessionService,
        RoomAdmissionCoordinator,
    ]:
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
        return repository, invites, sessions, coordinator


if __name__ == "__main__":
    unittest.main()
