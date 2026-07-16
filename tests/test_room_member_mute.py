from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.errors import RoomCommandRejected
from agentsassemble.room.event_broker import RoomEventBroker
from agentsassemble.room.member_mute import RoomMemberMuteService


class RoomMemberMuteServiceTests(unittest.TestCase):
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
        self.broker = RoomEventBroker()
        self.compatibility_writes: list[tuple[str, str, bool]] = []
        self.pending_assignments: list[tuple[str, str]] = []
        self.service = RoomMemberMuteService(
            store=self.store,
            broker=self.broker,
            assign_pending=self._assign_pending,
            compatibility_mute_writer=lambda room_id, participant_id, muted: (
                self.compatibility_writes.append(
                    (room_id, participant_id, muted)
                )
            ),
        )

    def tearDown(self) -> None:
        self.broker.close()
        self.store.close()
        self.temporary_directory.cleanup()

    def _assign_pending(self, room_id: str, participant_id: str) -> bool:
        self.pending_assignments.append((room_id, participant_id))
        return True

    @staticmethod
    def _fail_compatibility_write(
        _room_id: str,
        _participant_id: str,
        _muted: bool,
    ) -> None:
        raise RuntimeError("sync failed")

    def _update_in_unit(self, muted: bool) -> dict[str, object]:
        payload = {"participant_id": "codex", "muted": muted}
        with RoomCommandUnitOfWork(
            self.store,
            room_id="general",
            principal_id="browser:host",
            request_id=f"mute-{muted}",
            action="participant.mute",
            payload=payload,
        ) as unit:
            result = self.service.update_in_unit(
                "codex",
                muted,
                {"participant_id": "codex", "muted": muted},
                unit=unit,
            )
            unit.build_ack(result)
            unit.record_ack()
        return result

    def test_update_persists_canonical_mute_and_event_in_one_transaction(self) -> None:
        result = self._update_in_unit(True)

        self.assertTrue(result["participant"]["muted"])
        self.assertTrue(self.store.participant("general", "codex")["muted"])
        event = [
            event
            for event in self.store.read_events("general")
            if event["type"] == "participant_muted"
        ][-1]
        self.assertTrue(event["muted"])

    def test_muting_busy_agent_interrupts_its_active_bridge(self) -> None:
        identity = {
            "meeting_id": "general",
            "agent_id": "codex",
            "session_id": "codex",
            "client_type": "agent_bridge",
        }
        channel = self.broker.connect(identity)
        self.broker.activate_bridge(channel)

        self.service.apply_after_commit("general", "codex", True)

        self.assertEqual(
            self.compatibility_writes,
            [("general", "codex", True)],
        )
        self.assertIn(
            {"op": "agent.control", "action": "interrupt"},
            channel.drain(),
        )

    def test_unmuting_agent_assigns_its_pending_work(self) -> None:
        self.service.apply_after_commit("general", "codex", False)

        self.assertEqual(
            self.pending_assignments,
            [("general", "codex")],
        )

    def test_compatibility_sync_failure_is_explicit(self) -> None:
        self.service = RoomMemberMuteService(
            store=self.store,
            broker=self.broker,
            assign_pending=self._assign_pending,
            compatibility_mute_writer=self._fail_compatibility_write,
        )

        with self.assertRaises(RoomCommandRejected) as raised:
            self.service.apply_after_commit("general", "codex", True)

        self.assertEqual(raised.exception.code, "compatibility_sync_failed")
        self.assertEqual(self.pending_assignments, [])


if __name__ == "__main__":
    unittest.main()
