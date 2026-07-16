from __future__ import annotations

import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.participant_kick import RoomParticipantKickService


class RoomParticipantKickServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = RoomStore(self.root)
        self.store.create_room("general")
        self.store.upsert_participant(
            "general",
            {
                "participant_id": "codex",
                "display_name": "Codex",
                "participant_type": "agent",
                "role": "agent",
                "status": "joined",
            },
        )
        self.store.upsert_session(
            "general",
            {
                "session_id": "codex",
                "participant_id": "codex",
                "runtime_status": "busy",
            },
        )
        self.stops: list[tuple[str, str, str]] = []
        self.revocations: list[tuple[str, str]] = []
        self.disconnects: list[tuple[str, str]] = []
        self.membership_removals: list[tuple[str, str]] = []
        self.voice_leaves: list[tuple[str, str]] = []
        self.provider_removals: list[tuple[str, str]] = []
        self.service = self._service()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def _service(
        self,
        *,
        stop_agent: Callable[[str, str, str], object] | None = None,
    ) -> RoomParticipantKickService:
        return RoomParticipantKickService(
            store=self.store,
            stop_agent=stop_agent or self._stop_agent,
            revoke_participant_sessions=self._revoke_sessions,
            disconnect_participant=lambda room_id, participant_id: (
                self.disconnects.append((room_id, participant_id))
            ),
            remove_membership=lambda room_id, participant_id: (
                self.membership_removals.append((room_id, participant_id))
                or True
            ),
            leave_all_voice=lambda room_id, participant_id: (
                self.voice_leaves.append((room_id, participant_id))
            ),
            remove_provider=lambda room_id, participant_id: (
                self.provider_removals.append((room_id, participant_id))
            ),
        )

    def _stop_agent(
        self,
        room_id: str,
        participant_id: str,
        operation_id: str,
    ) -> None:
        self.stops.append((room_id, participant_id, operation_id))

    def _revoke_sessions(
        self,
        room_id: str,
        participant_id: str,
    ) -> int:
        self.revocations.append((room_id, participant_id))
        return 2

    def _finalize(
        self,
        cleanup: dict[str, object],
        *,
        operation_id: str = "kick-1",
    ) -> dict[str, object]:
        payload = {"participant_id": "codex"}
        with RoomCommandUnitOfWork(
            self.store,
            room_id="general",
            principal_id="browser:host",
            request_id="kick-request",
            action="participant.kick",
            payload=payload,
        ) as unit:
            result = self.service.finalize_in_unit(
                "codex",
                operation_id=operation_id,
                cleanup=cleanup,
                unit=unit,
            )
            unit.build_ack(result)
            unit.record_ack()
        return result

    def test_applied_effects_are_reused_without_repeating_cleanup(self) -> None:
        participant = self.service.prepare_intent(
            "general",
            "codex",
            operation_id="kick-1",
        )

        first = self.service.apply_effects(
            "general",
            participant,
            operation_id="kick-1",
        )
        prepared = self.store.participant("general", "codex")
        second = self.service.apply_effects(
            "general",
            prepared,
            operation_id="kick-1",
        )

        self.assertEqual(first, second)
        self.assertEqual(
            self.stops,
            [("general", "codex", "kick-1:stop")],
        )
        self.assertEqual(self.revocations, [("general", "codex")])
        self.assertEqual(self.disconnects, [("general", "codex")])
        self.assertEqual(
            self.membership_removals,
            [("general", "codex")],
        )
        self.assertEqual(self.voice_leaves, [("general", "codex")])

    def test_agent_stop_failure_becomes_cleanup_warning(self) -> None:
        def reject_stop(
            _room_id: str,
            _participant_id: str,
            _operation_id: str,
        ) -> None:
            raise RoomCommandRejected(
                "stop was not confirmed",
                code="external_stop_unconfirmed",
            )

        self.service = self._service(stop_agent=reject_stop)
        participant = self.service.prepare_intent(
            "general",
            "codex",
            operation_id="kick-1",
        )

        cleanup = self.service.apply_effects(
            "general",
            participant,
            operation_id="kick-1",
        )

        self.assertIn(
            "external_stop_unconfirmed",
            cleanup["cleanup_warning"],
        )
        self.assertEqual(cleanup["revoked_sessions"], 2)
        self.assertTrue(cleanup["removed_member"])
        self.assertEqual(self.revocations, [("general", "codex")])

    def test_finalize_requires_matching_completed_cleanup(self) -> None:
        self.service.prepare_intent(
            "general",
            "codex",
            operation_id="kick-1",
        )

        with self.assertRaises(RoomCommandRejected) as raised:
            self._finalize({})

        self.assertEqual(
            raised.exception.code,
            "moderation_cleanup_incomplete",
        )
        self.assertEqual(
            self.store.participant("general", "codex")["status"],
            "joined",
        )

    def test_finalize_marks_kicked_and_clears_private_intent(self) -> None:
        participant = self.service.prepare_intent(
            "general",
            "codex",
            operation_id="kick-1",
        )
        cleanup = self.service.apply_effects(
            "general",
            participant,
            operation_id="kick-1",
        )

        result = self._finalize(cleanup)
        self.service.apply_after_commit(
            "general",
            self.store.participant("general", "codex"),
        )

        self.assertEqual(result["participant"]["status"], "kicked")
        self.assertFalse(
            any(
                key.startswith("moderation_intent_")
                for key in result["participant"]
            )
        )
        stored = self.store.participant("general", "codex")
        self.assertEqual(stored["moderation_intent_action"], "")
        self.assertEqual(
            self.provider_removals,
            [("general", "codex")],
        )
        kicked_events = [
            event
            for event in self.store.read_events("general")
            if event["type"] == "participant_kicked"
        ]
        self.assertEqual(len(kicked_events), 1)

    def test_prepare_rejects_host_missing_and_conflicting_operation(self) -> None:
        with self.assertRaises(RoomCommandRejected) as host:
            self.service.prepare_intent(
                "general",
                "operator-local",
                operation_id="kick-host",
            )
        with self.assertRaises(RoomCommandRejected) as missing:
            self.service.prepare_intent(
                "general",
                "missing",
                operation_id="kick-missing",
            )
        self.service.prepare_intent(
            "general",
            "codex",
            operation_id="kick-1",
        )
        with self.assertRaises(RoomCommandRejected) as conflict:
            self.service.prepare_intent(
                "general",
                "codex",
                operation_id="kick-2",
            )

        self.assertEqual(host.exception.code, "permission_denied")
        self.assertEqual(missing.exception.code, "not_found")
        self.assertEqual(conflict.exception.code, "operation_in_progress")


if __name__ == "__main__":
    unittest.main()
