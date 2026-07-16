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
        self.scheduled_cleanup: list[
            tuple[float, Callable[[], None]]
        ] = []
        self.service = RoomParticipantLeaveService(
            remove_membership=lambda room_id, participant_id: (
                self.membership_removals.append((room_id, participant_id))
            ),
            leave_all_voice=lambda room_id, participant_id: (
                self.voice_leaves.append((room_id, participant_id))
            ),
            revoke_participant_sessions=lambda room_id, participant_id: (
                self.session_revocations.append((room_id, participant_id))
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


if __name__ == "__main__":
    unittest.main()
