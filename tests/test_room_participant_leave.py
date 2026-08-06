from __future__ import annotations

import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.participant_leave import RoomParticipantLeaveService


class RoomParticipantLeaveServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = RoomStore(self.root)
        self.store.create_room("general")
        self.store.upsert_participant(
            "general",
            {
                "participant_id": "member",
                "display_name": "Member",
                "participant_type": "human",
                "role": "member",
                "status": "joined",
            },
        )
        self.membership_removals: list[tuple[str, str]] = []
        self.voice_leaves: list[tuple[str, str]] = []
        self.session_revocations: list[tuple[str, str]] = []
        self.disconnections: list[tuple[str, str]] = []
        self.agent_stops: list[tuple[str, str, str]] = []
        self.provider_removals: list[tuple[str, str]] = []
        self.scheduled_cleanup: list[
            tuple[float, Callable[[], None]]
        ] = []
        self.service = RoomParticipantLeaveService(
            store=self.store,
            remove_membership=lambda room_id, participant_id: (
                self.membership_removals.append((room_id, participant_id))
            ),
            leave_all_voice=lambda room_id, participant_id: (
                self.voice_leaves.append((room_id, participant_id))
            ),
            revoke_participant_sessions=lambda room_id, participant_id: (
                self.session_revocations.append((room_id, participant_id))
            ),
            disconnect_participant=lambda room_id, participant_id: (
                self.disconnections.append((room_id, participant_id))
            ),
            stop_agent=lambda room_id, participant_id, operation_id: (
                self.agent_stops.append((room_id, participant_id, operation_id))
            ),
            remove_provider=lambda room_id, participant_id: (
                self.provider_removals.append((room_id, participant_id))
            ),
            schedule_cleanup=lambda delay, callback: (
                self.scheduled_cleanup.append((delay, callback))
            ),
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def _update_in_unit(
        self,
        participant_id: str = "member",
        *,
        is_owner: bool = False,
        owned_agent_ids: tuple[str, ...] = (),
    ) -> dict[str, object]:
        payload: dict[str, object] = {}
        with RoomCommandUnitOfWork(
            self.store,
            room_id="general",
            principal_id=f"browser:{participant_id}",
            request_id=f"leave-{participant_id}",
            action="participant.leave",
            payload=payload,
        ) as unit:
            result = self.service.update_in_unit(
                participant_id,
                is_owner=is_owner,
                owned_agent_ids=owned_agent_ids,
                unit=unit,
            )
            unit.build_ack(result)
            unit.record_ack()
        return result

    def test_update_persists_left_status_and_event_in_one_transaction(self) -> None:
        result = self._update_in_unit()

        self.assertEqual(result["participant"]["status"], "left")
        self.assertTrue(result["revocation_scheduled"])
        self.assertEqual(
            self.store.participant("general", "member")["status"],
            "left",
        )
        event = [
            event
            for event in self.store.read_events("general")
            if event["type"] == "participant_left"
        ][-1]
        self.assertEqual(event["participant_id"], "member")

    def test_owner_leave_is_rejected_without_mutation(self) -> None:
        with self.assertRaises(RoomCommandRejected) as raised:
            self._update_in_unit(is_owner=True)

        self.assertEqual(
            raised.exception.code,
            "owner_must_transfer_or_delete",
        )
        self.assertEqual(
            self.store.participant("general", "member")["status"],
            "joined",
        )
        self.assertFalse(
            any(
                event["type"] == "participant_left"
                for event in self.store.read_events("general")
            )
        )

    def test_missing_participant_is_rejected(self) -> None:
        with self.assertRaises(RoomCommandRejected) as raised:
            self._update_in_unit("missing")

        self.assertEqual(raised.exception.code, "not_found")

    def test_post_commit_cleanup_revokes_access_after_ack_delay(self) -> None:
        self.service.apply_after_commit("general", "member")

        self.assertEqual(
            self.membership_removals,
            [("general", "member")],
        )
        self.assertEqual(
            self.voice_leaves,
            [("general", "member")],
        )
        self.assertEqual(self.session_revocations, [])
        self.assertEqual(len(self.scheduled_cleanup), 1)
        delay, callback = self.scheduled_cleanup[0]
        self.assertEqual(delay, 0.1)

        callback()

        self.assertEqual(
            self.session_revocations,
            [("general", "member")],
        )

    def test_owner_leave_marks_owned_agents_left_in_the_same_transaction(self) -> None:
        self.store.upsert_participant(
            "general",
            {
                "participant_id": "member-agent",
                "display_name": "Member Agent",
                "participant_type": "agent",
                "role": "agent",
                "owner_id": "member-user",
                "status": "joined",
            },
        )
        self.store.upsert_participant(
            "general",
            {
                "participant_id": "other-agent",
                "display_name": "Other Agent",
                "participant_type": "agent",
                "role": "agent",
                "owner_id": "other-user",
                "status": "joined",
            },
        )
        for agent_id in ("member-agent", "other-agent"):
            self.store.upsert_session(
                "general",
                {
                    "session_id": agent_id,
                    "participant_id": agent_id,
                    "runtime_status": "available",
                    "enabled": False,
                },
            )

        owned = self.service.owned_agent_ids(
            "general",
            owner_ids=("member", "member-user"),
        )
        result = self._update_in_unit(owned_agent_ids=tuple(owned))

        self.assertEqual(result["owned_agent_ids"], ["member-agent"])
        self.assertEqual(
            self.store.participant("general", "member-agent")["status"],
            "left",
        )
        self.assertEqual(
            self.store.participant("general", "other-agent")["status"],
            "joined",
        )
        owner_leave_events = [
            event
            for event in self.store.read_events("general")
            if event["type"] == "participant_left"
            and event.get("reason") == "owner_left"
        ]
        self.assertEqual(
            [event["participant_id"] for event in owner_leave_events],
            ["member-agent"],
        )

    def test_post_commit_cleanup_stops_and_removes_owned_agents(self) -> None:
        self.store.upsert_participant(
            "general",
            {
                "participant_id": "member-agent",
                "display_name": "Member Agent",
                "participant_type": "agent",
                "role": "agent",
                "owner_id": "member",
                "status": "left",
            },
        )
        self.store.upsert_session(
            "general",
            {
                "session_id": "member-agent",
                "participant_id": "member-agent",
                "runtime_status": "idle",
                "enabled": True,
            },
        )

        self.service.apply_after_commit(
            "general",
            "member",
            owned_agent_ids=("member-agent",),
            operation_id="leave-operation",
        )

        self.assertEqual(
            self.agent_stops,
            [
                (
                    "general",
                    "member-agent",
                    "leave-operation:owned-agent:member-agent",
                )
            ],
        )
        self.assertEqual(
            self.session_revocations,
            [("general", "member-agent")],
        )
        self.assertEqual(
            self.disconnections,
            [("general", "member-agent")],
        )
        self.assertEqual(
            self.provider_removals,
            [("general", "member-agent")],
        )
        self.assertEqual(
            self.membership_removals,
            [("general", "member-agent"), ("general", "member")],
        )

    def test_failed_owned_agent_shutdown_is_durable_and_retried_after_restart(self) -> None:
        self.store.upsert_participant(
            "general",
            {
                "participant_id": "member-agent",
                "display_name": "Member Agent",
                "participant_type": "agent",
                "role": "agent",
                "owner_id": "member",
                "status": "left",
            },
        )
        self.store.upsert_session(
            "general",
            {
                "session_id": "member-agent",
                "participant_id": "member-agent",
                "runtime_status": "idle",
                "enabled": True,
            },
        )
        attempts = 0

        def fail_once(
            room_id: str,
            participant_id: str,
            operation_id: str,
        ) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RoomCommandRejected(
                    "Provider shutdown was not confirmed.",
                    code="runtime_stop_unconfirmed",
                )

        service = RoomParticipantLeaveService(
            store=self.store,
            remove_membership=lambda room_id, participant_id: None,
            leave_all_voice=lambda room_id, participant_id: None,
            revoke_participant_sessions=lambda room_id, participant_id: None,
            disconnect_participant=lambda room_id, participant_id: None,
            stop_agent=fail_once,
            remove_provider=lambda room_id, participant_id: (
                self.provider_removals.append((room_id, participant_id))
            ),
            schedule_cleanup=lambda delay, callback: (
                self.scheduled_cleanup.append((delay, callback))
            ),
        )

        service.apply_after_commit(
            "general",
            "member",
            owned_agent_ids=("member-agent",),
            operation_id="leave-operation",
        )

        pending = self.store.participant("general", "member-agent")
        self.assertTrue(pending["moderation_cleanup_pending"])
        self.assertIn(
            "runtime_stop_unconfirmed",
            pending["moderation_cleanup_warning"],
        )
        self.assertEqual(self.provider_removals, [])

        # Simulate restart: scheduled callbacks disappear and a new service
        # reconstructs the durable cleanup work from room state.
        self.scheduled_cleanup.clear()
        restarted = RoomParticipantLeaveService(
            store=self.store,
            remove_membership=lambda room_id, participant_id: None,
            leave_all_voice=lambda room_id, participant_id: None,
            revoke_participant_sessions=lambda room_id, participant_id: None,
            disconnect_participant=lambda room_id, participant_id: None,
            stop_agent=fail_once,
            remove_provider=lambda room_id, participant_id: (
                self.provider_removals.append((room_id, participant_id))
            ),
            schedule_cleanup=lambda delay, callback: (
                self.scheduled_cleanup.append((delay, callback))
            ),
        )
        restarted.reconcile_pending()
        self.assertEqual(len(self.scheduled_cleanup), 1)

        self.scheduled_cleanup.pop()[1]()

        recovered = self.store.participant("general", "member-agent")
        self.assertFalse(recovered["moderation_cleanup_pending"])
        self.assertEqual(
            self.provider_removals,
            [("general", "member-agent")],
        )


if __name__ == "__main__":
    unittest.main()
