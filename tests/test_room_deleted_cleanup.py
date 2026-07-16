from __future__ import annotations

import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.room.deleted_cleanup import RoomDeletedCleanupService
from agentsassemble.room.event_broker import RoomEventBroker


class RoomDeletedCleanupServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = RoomStore(self.root)
        self.store.create_room("general", label="Council")
        self.store.delete_room(
            "general",
            reason="test",
            tombstone={
                "principal_id": "browser:owner",
                "request_id": "delete-1",
                "action": "room.delete",
                "payload_hash": "hash",
                "result": {
                    "op": "ack",
                    "request_id": "delete-1",
                    "accepted": True,
                    "action": "room.delete",
                    "result": {
                        "room_id": "general",
                        "deleted": True,
                        "cleanup_warnings": [],
                    },
                    "deduplicated": False,
                },
            },
            cleanup_status="pending",
            room_name="Council",
        )
        self.broker = RoomEventBroker()
        self.channel = self.broker.connect(
            {
                "meeting_id": "general",
                "agent_id": "owner",
                "client_type": "browser",
            },
        )
        self.provider_registry = Mock()
        self.invite_revocations: list[str] = []
        self.session_revocations: list[str] = []
        self.workflow_purges: list[str] = []
        self.identity_deletions: list[str] = []
        self.listener_removals: list[str] = []
        self.scheduled_cleanup: list[
            tuple[float, Callable[[], None]]
        ] = []
        for path in (
            self.root / "rooms" / "general" / "media",
            self.root / "meetings" / "general",
        ):
            path.mkdir(parents=True, exist_ok=True)
            (path / "artifact.txt").write_text(
                "delete me",
                encoding="utf-8",
            )
        self.service = RoomDeletedCleanupService(
            store=self.store,
            broker=self.broker,
            provider_registry=self.provider_registry,
            output_root=self.root,
            revoke_room_invites=self._revoke_invites,
            revoke_room_sessions=self._revoke_sessions,
            purge_terminal_admission_workflows=self._purge_workflows,
            delete_identity_room=lambda room_id: (
                self.identity_deletions.append(room_id)
            ),
            remove_event_listener=lambda room_id: (
                self.listener_removals.append(room_id)
            ),
            schedule_cleanup=lambda delay, callback: (
                self.scheduled_cleanup.append((delay, callback))
            ),
        )

    def tearDown(self) -> None:
        self.broker.close()
        self.store.close()
        self.temporary_directory.cleanup()

    def _revoke_invites(self, room_id: str) -> int:
        self.invite_revocations.append(room_id)
        return 1

    def _revoke_sessions(self, room_id: str) -> int:
        self.session_revocations.append(room_id)
        return 2

    def _purge_workflows(self, room_id: str) -> int:
        self.workflow_purges.append(room_id)
        return 3

    @staticmethod
    def _ack() -> dict[str, object]:
        return {
            "op": "ack",
            "request_id": "delete-1",
            "accepted": True,
            "action": "room.delete",
            "result": {
                "room_id": "general",
                "deleted": True,
                "cleanup_warnings": [],
            },
            "deduplicated": False,
        }

    def test_complete_cleans_supporting_state_and_delays_disconnect(self) -> None:
        completed = self.service.complete(
            "general",
            "Council",
            self._ack(),
            False,
        )

        self.assertEqual(completed["result"]["revoked_invites"], 1)
        self.assertEqual(completed["result"]["revoked_sessions"], 2)
        self.assertEqual(
            completed["result"]["purged_admission_workflows"],
            3,
        )
        self.assertEqual(self.identity_deletions, ["general"])
        self.assertEqual(self.listener_removals, ["general"])
        self.provider_registry.remove_room.assert_called_once_with("general")
        self.assertFalse((self.root / "rooms" / "general").exists())
        self.assertFalse((self.root / "meetings" / "general").exists())
        self.assertEqual(
            self.store.deleted_room_record("general")["cleanup_status"],
            "complete",
        )
        self.assertIn(
            {
                "op": "room_deleted",
                "room_id": "general",
                "room_name": "Council",
            },
            self.channel.drain(),
        )
        self.assertFalse(self.channel.closed)
        self.assertEqual(len(self.scheduled_cleanup), 1)
        delay, disconnect = self.scheduled_cleanup[0]
        self.assertEqual(delay, 0.1)

        disconnect()

        self.assertTrue(self.channel.closed)

    def test_retry_is_safe_after_artifacts_are_already_absent(self) -> None:
        self.service.complete(
            "general",
            "Council",
            self._ack(),
            False,
        )

        retried = self.service.complete(
            "general",
            "Council",
            self._ack(),
            True,
        )

        self.assertTrue(retried["deduplicated"])
        self.assertEqual(
            self.store.deleted_room_record("general")["cleanup_status"],
            "complete",
        )
        self.assertEqual(self.invite_revocations, ["general", "general"])
        self.assertEqual(self.session_revocations, ["general", "general"])


if __name__ == "__main__":
    unittest.main()
