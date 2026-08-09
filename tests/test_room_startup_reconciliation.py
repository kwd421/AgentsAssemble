from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from agentsassemble.persistence.local.room.repository import RoomStore
from agentsassemble.room.startup_reconciliation import RoomStartupSessionReconciler


class RoomStartupSessionReconcilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = RoomStore(self.root)
        self.store.create_room("general")
        self.attention_resets: list[tuple[str, str, list[str]]] = []
        self.reconciler = RoomStartupSessionReconciler(
            store=self.store,
            reconcile_session_attention=self._reconcile_attention,
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def _reconcile_attention(
        self,
        room_id: str,
        session: dict[str, object],
        *,
        pending_event_ids: list[str],
    ) -> dict[str, object]:
        self.attention_resets.append(
            (
                room_id,
                str(session.get("session_id") or ""),
                list(pending_event_ids),
            )
        )
        return {
            "pending_event_ids": list(pending_event_ids),
            "active_attention_job_id": "",
            "active_attention_lease_id": "",
            "active_attention_source_event_id": "",
        }

    def test_active_session_returns_inflight_work_to_pending_and_detaches(self) -> None:
        self.store.upsert_participant(
            "general",
            {
                "participant_id": "codex",
                "display_name": "Codex",
                "participant_type": "agent",
                "status": "joined",
            },
        )
        self.store.upsert_session(
            "general",
            {
                "session_id": "codex",
                "participant_id": "codex",
                "status": "attached",
                "runtime_status": "busy",
                "inflight_event_ids": ["event-1", "event-2"],
                "pending_event_ids": ["event-2", "event-3"],
                "bridge_handle_id": "lost-handle",
                "active_turn_id": "turn-1",
                "turn_phase": "streaming",
                "pending_provider_request": {
                    "provider_request_id": "approval-before-restart",
                    "participant_id": "codex",
                    "owner_id": "operator-local",
                    "status": "open",
                },
            },
        )

        self.reconciler.reconcile()

        session = self.store.session("general", "codex")
        self.assertEqual(session["runtime_status"], "disconnected")
        self.assertEqual(session["status"], "unavailable")
        self.assertEqual(session["pending_event_ids"], ["event-1", "event-2", "event-3"])
        self.assertEqual(session["inflight_event_ids"], [])
        self.assertEqual(session["bridge_handle_id"], "")
        self.assertEqual(session["active_turn_id"], "")
        self.assertEqual(session["pending_provider_request"], {})
        self.assertTrue(session["recovery_required"])
        self.assertEqual(
            self.store.participant("general", "codex")["status"],
            "detached",
        )
        self.assertEqual(
            self.attention_resets,
            [("general", "codex", ["event-1", "event-2", "event-3"])],
        )
        failed = next(
            event
            for event in self.store.read_events("general")
            if event["type"] == "provider_request_resolved"
        )
        self.assertEqual(failed["provider_request"]["status"], "failed")
        self.assertEqual(failed["reason_code"], "provider_request_server_restarted")

    def test_stopped_session_is_left_unchanged(self) -> None:
        self.store.upsert_session(
            "general",
            {
                "session_id": "codex",
                "participant_id": "codex",
                "status": "available",
                "runtime_status": "stopped",
                "pending_event_ids": ["event-1"],
            },
        )

        self.reconciler.reconcile()

        session = self.store.session("general", "codex")
        self.assertEqual(session["runtime_status"], "stopped")
        self.assertEqual(session["pending_event_ids"], ["event-1"])
        self.assertEqual(self.attention_resets, [])

    def test_rolling_replacement_relaunches_each_enabled_server_owned_session(self) -> None:
        self.store.upsert_participant(
            "general",
            {
                "participant_id": "codex",
                "display_name": "Codex",
                "participant_type": "agent",
                "status": "joined",
            },
        )
        self.store.upsert_session(
            "general",
            {
                "session_id": "codex",
                "participant_id": "codex",
                "enabled": True,
                "runtime_status": "idle",
                "process_ownership": "server",
                "bridge_handle_id": "previous-launch",
            },
        )
        starts: list[tuple[str, str, bool]] = []

        def start_session(
            room_id: str,
            session_id: str,
            **options: object,
        ) -> dict[str, object]:
            starts.append(
                (room_id, session_id, bool(options.get("automatic_recovery")))
            )
            return self.store.update_session_fields(
                room_id,
                session_id,
                runtime_status="starting",
                bridge_handle_id="replacement-launch",
            )

        reconciler = RoomStartupSessionReconciler(
            store=self.store,
            reconcile_session_attention=self._reconcile_attention,
            lock=threading.RLock(),
            start_session=start_session,
        )

        restarted = reconciler.restart_preserved_server_sessions(
            server_url="http://127.0.0.1:8765",
            ticket_issuer=lambda _identity: {"ticket": "replacement-ticket"},
        )

        session = self.store.session("general", "codex")
        self.assertEqual(starts, [("general", "codex", True)])
        self.assertEqual(session["runtime_status"], "starting")
        self.assertEqual(session["bridge_handle_id"], "replacement-launch")
        self.assertTrue(session["recovery_required"])
        self.assertEqual(len(restarted), 1)


if __name__ == "__main__":
    unittest.main()
