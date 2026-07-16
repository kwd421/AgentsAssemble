from __future__ import annotations

import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.room.deletion import RoomDeletionService
from agentsassemble.room.errors import RoomCommandRejected


class RoomDeletionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = RoomStore(self.root)
        self.store.create_room("general", label="#general")
        self.identity_room = {"label": "Council"}
        self.bridges: set[tuple[str, str]] = set()
        self.stops: list[tuple[str, str, str]] = []
        self.revocations: list[tuple[str, str]] = []
        self.disconnects: list[tuple[str, str]] = []
        self.completions: list[tuple[str, str, bool]] = []
        self.service = self._service()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def _service(
        self,
        *,
        stop_agent: Callable[[str, str, str], object] | None = None,
        complete_cleanup: Callable[
            [str, str, dict[str, object], bool],
            dict[str, object],
        ]
        | None = None,
    ) -> RoomDeletionService:
        return RoomDeletionService(
            store=self.store,
            identity_room=lambda _room_id: dict(self.identity_room),
            has_bridge=lambda room_id, session_id: (
                room_id,
                session_id,
            )
            in self.bridges,
            stop_agent=stop_agent or self._stop_agent,
            revoke_participant_sessions=self._revoke_sessions,
            disconnect_participant=lambda room_id, participant_id: (
                self.disconnects.append((room_id, participant_id))
            ),
            complete_cleanup=complete_cleanup or self._complete_cleanup,
        )

    def _stop_agent(
        self,
        room_id: str,
        session_id: str,
        operation_id: str,
    ) -> None:
        self.stops.append((room_id, session_id, operation_id))

    def _revoke_sessions(
        self,
        room_id: str,
        participant_id: str,
    ) -> int:
        self.revocations.append((room_id, participant_id))
        return 1

    def _complete_cleanup(
        self,
        room_id: str,
        room_name: str,
        ack: dict[str, object],
        deduplicated: bool,
    ) -> dict[str, object]:
        self.completions.append((room_id, room_name, deduplicated))
        result = dict(ack.get("result") or {})
        completed = {
            **ack,
            "result": {
                **result,
                "revoked_invites": 1,
                "revoked_sessions": 2,
                "purged_admission_workflows": 3,
            },
            "deduplicated": deduplicated,
        }
        self.store.update_deleted_room_record(
            room_id,
            result={
                **completed,
                "deduplicated": False,
            },
            cleanup_status="complete",
        )
        return completed

    def _delete(
        self,
        *,
        confirmation_name: str = "Council",
        is_owner: bool = True,
        request_id: str = "delete-1",
    ) -> dict[str, object]:
        return self.service.delete(
            "general",
            confirmation_name,
            is_owner=is_owner,
            request_id=request_id,
            principal_id="browser:owner",
            payload_hash=f"hash:{confirmation_name}",
            operation_id="delete-operation",
        )

    def test_delete_requires_owner_and_exact_confirmation(self) -> None:
        with self.assertRaises(RoomCommandRejected) as denied:
            self._delete(is_owner=False)
        with self.assertRaises(RoomCommandRejected) as mismatch:
            self._delete(confirmation_name="Wrong")

        self.assertEqual(denied.exception.code, "permission_denied")
        self.assertEqual(mismatch.exception.code, "confirmation_mismatch")
        self.assertFalse(self.store.room_is_deleted("general"))

    def test_delete_stops_active_session_and_completes_tombstone(self) -> None:
        self.store.upsert_session(
            "general",
            {
                "session_id": "codex",
                "participant_id": "codex",
                "process_ownership": "server",
                "runtime_status": "busy",
            },
        )

        result = self._delete()

        self.assertTrue(result["result"]["deleted"])
        self.assertEqual(len(self.stops), 1)
        self.assertEqual(self.stops[0][:2], ("general", "codex"))
        self.assertEqual(len(self.stops[0][2]), 64)
        self.assertTrue(self.store.room_is_deleted("general"))
        self.assertEqual(
            self.store.deleted_room_record("general")["cleanup_status"],
            "complete",
        )
        self.assertEqual(
            self.completions,
            [("general", "Council", False)],
        )

    def test_server_cleanup_failure_keeps_canonical_room(self) -> None:
        self.store.upsert_session(
            "general",
            {
                "session_id": "codex",
                "participant_id": "codex",
                "process_ownership": "server",
                "runtime_status": "busy",
            },
        )

        def fail_stop(
            _room_id: str,
            _session_id: str,
            _operation_id: str,
        ) -> None:
            raise RoomCommandRejected(
                "provider cleanup failed",
                code="provider_cleanup_failed",
            )

        self.service = self._service(stop_agent=fail_stop)

        with self.assertRaises(RoomCommandRejected) as raised:
            self._delete()

        self.assertEqual(raised.exception.code, "room_cleanup_failed")
        self.assertFalse(self.store.room_is_deleted("general"))
        self.assertTrue(self.store.room("general"))

    def test_disconnected_external_session_is_revoked_without_stop(self) -> None:
        self.store.upsert_session(
            "general",
            {
                "session_id": "external",
                "participant_id": "external",
                "process_ownership": "external",
                "external_owned": True,
                "runtime_status": "disconnected",
            },
        )

        result = self._delete()

        self.assertEqual(self.stops, [])
        self.assertEqual(
            self.revocations,
            [("general", "external")],
        )
        self.assertEqual(
            self.disconnects,
            [("general", "external")],
        )
        self.assertIn(
            "without claiming provider shutdown",
            result["result"]["cleanup_warnings"][0],
        )

    def test_pending_tombstone_resume_does_not_repeat_agent_stop(self) -> None:
        self.store.upsert_session(
            "general",
            {
                "session_id": "codex",
                "participant_id": "codex",
                "process_ownership": "server",
                "runtime_status": "busy",
            },
        )

        with patch.object(
            self.store,
            "update_deleted_room_record",
            side_effect=RuntimeError("cleanup write failed"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "cleanup write failed",
            ):
                self._delete()

        tombstone = self.store.deleted_room_record("general")
        resumed = self.service.resume(
            "general",
            principal_id="browser:owner",
            request_id="delete-1",
            payload_hash="hash:Council",
            tombstone=tombstone,
        )

        self.assertTrue(resumed["deduplicated"])
        self.assertEqual(len(self.stops), 1)
        self.assertEqual(
            self.store.deleted_room_record("general")["cleanup_status"],
            "complete",
        )


if __name__ == "__main__":
    unittest.main()
