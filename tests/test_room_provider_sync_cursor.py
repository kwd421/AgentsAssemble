import tempfile
import unittest
from pathlib import Path

from agentsassemble.providers.launch_specs import NativeCliProviderSpec
from agentsassemble.room_provider_sync_cursor import (
    ProviderSyncCursorParityError,
    ProviderSyncCursorReconciler,
    canonical_provider_sync_seq,
)
from agentsassemble.room_realtime import RoomRealtimeController
from tests.room_realtime_test_support import memory_room_access_services
from agentsassemble.room_store import RoomStore


class ProviderSyncCursorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = RoomStore(self.root)
        self.store.create_room("general", label="General")
        self.store.upsert_participant(
            "general",
            {
                "participant_id": "agent-a",
                "display_name": "Agent A",
                "participant_type": "agent",
                "role": "agent",
                "status": "joined",
            },
        )

    def tearDown(self):
        self.temp.cleanup()

    def _message(self, content: str) -> dict[str, object]:
        return self.store.append_event(
            "general",
            "message_final",
            participant_id="operator-local",
            actor_id="operator-local",
            actor_type="human",
            content=content,
        )

    def _session(self, *, event_id: str = "", sequence: int = 0) -> dict[str, object]:
        session, _ = self.store.upsert_session(
            "general",
            {
                "session_id": "agent-a",
                "participant_id": "agent-a",
                "display_name": "Agent A",
                "status": "attached",
                "runtime_status": "stopped",
                "process_ownership": "external",
                "last_provider_sync_event_id": event_id,
                "last_provider_sync_seq": sequence,
            },
        )
        return session

    def test_initializes_canonical_cursor_from_compatibility_session(self):
        message = self._message("delivered")
        self._session(event_id=str(message["id"]), sequence=int(message["seq"]))

        report = ProviderSyncCursorReconciler(self.store).reconcile()

        session = self.store.session("general", "agent-a")
        state = self.store.attention_state("general", "agent-a")
        self.assertEqual(state.last_provider_sync_seq, message["seq"])
        self.assertEqual(
            canonical_provider_sync_seq(self.store, "general", "agent-a", session),
            message["seq"],
        )
        self.assertEqual(report.repairs[0]["code"], "canonical_cursor_initialized")

    def test_restores_compatibility_cursor_from_canonical_state(self):
        message = self._message("delivered")
        self._session()
        with self.store.transaction("general") as transaction:
            transaction.advance_attention_state(
                "agent-a",
                provider_sync_seq=int(message["seq"]),
            )

        report = ProviderSyncCursorReconciler(self.store).reconcile()

        session = self.store.session("general", "agent-a")
        self.assertEqual(session["last_provider_sync_seq"], message["seq"])
        self.assertEqual(session["last_provider_sync_event_id"], message["id"])
        self.assertEqual(report.repairs[0]["code"], "compatibility_cursor_restored")

    def test_divergence_advances_to_monotonic_max_and_requires_recovery(self):
        first = self._message("first")
        second = self._message("second")
        self._session(event_id=str(first["id"]), sequence=int(first["seq"]))
        with self.store.transaction("general") as transaction:
            transaction.advance_attention_state(
                "agent-a",
                provider_sync_seq=int(second["seq"]),
            )

        report = ProviderSyncCursorReconciler(self.store).reconcile()

        session = self.store.session("general", "agent-a")
        self.assertEqual(session["last_provider_sync_seq"], second["seq"])
        self.assertEqual(session["last_provider_sync_event_id"], second["id"])
        self.assertTrue(session["recovery_required"])
        self.assertEqual(report.repairs[0]["code"], "cursor_divergence_reconciled")
        audits = [
            event
            for event in self.store.read_events("general")
            if event.get("type") == "provider_sync_cursor_reconciled"
        ]
        self.assertEqual(len(audits), 1)

    def test_invalid_future_cursor_is_reported_and_blocks_canonical_reads(self):
        message = self._message("latest")
        self._session(event_id=str(message["id"]), sequence=int(message["seq"]) + 10)

        report = ProviderSyncCursorReconciler(self.store).reconcile()

        session = self.store.session("general", "agent-a")
        self.assertTrue(session["recovery_required"])
        self.assertEqual(report.failures[0]["code"], "provider_sync_cursor_invalid")
        with self.assertRaises(ProviderSyncCursorParityError):
            canonical_provider_sync_seq(self.store, "general", "agent-a", session)

    def test_malformed_compatibility_cursor_is_not_treated_as_zero(self):
        self._session()
        self.store.update_session_fields(
            "general",
            "agent-a",
            last_provider_sync_seq="not-a-number",
        )

        report = ProviderSyncCursorReconciler(self.store).reconcile()

        session = self.store.session("general", "agent-a")
        self.assertTrue(session["recovery_required"])
        self.assertEqual(report.failures[0]["code"], "provider_sync_cursor_malformed")
        with self.assertRaises(ProviderSyncCursorParityError):
            canonical_provider_sync_seq(self.store, "general", "agent-a", session)

    def test_zero_cursor_rejects_a_nonempty_compatibility_event_id(self):
        session = self._session(event_id="missing-event", sequence=0)

        with self.assertRaises(ProviderSyncCursorParityError):
            canonical_provider_sync_seq(self.store, "general", "agent-a", session)

        report = ProviderSyncCursorReconciler(self.store).reconcile()
        repaired = self.store.session("general", "agent-a")
        self.assertEqual(repaired["last_provider_sync_event_id"], "")
        self.assertEqual(report.repairs[0]["code"], "compatibility_event_id_restored")

    def test_controller_initializes_new_session_with_cursor_parity(self):
        message = self._message("existing room context")
        access = memory_room_access_services()
        controller = RoomRealtimeController(
            self.root,
            **access.controller_kwargs(),
            repository=self.store,
            providers=[
                NativeCliProviderSpec(
                    agent_id="codex",
                    display_name="Codex",
                    command=("codex",),
                    cwd=str(self.root),
                )
            ],
        )
        try:
            session = self.store.session("general", "codex")
            state = self.store.attention_state("general", "codex")
            self.assertEqual(session["last_provider_sync_event_id"], message["id"])
            self.assertEqual(session["last_provider_sync_seq"], message["seq"])
            self.assertEqual(state.last_provider_sync_seq, message["seq"])
            diagnostics = controller.attention_active_diagnostics()
            self.assertEqual(
                diagnostics["provider_sync_cursor_reconciliation"]["failure_count"],
                0,
            )
        finally:
            controller.close()

    def test_controller_runs_cursor_reconciliation_before_serving_turns(self):
        message = self._message("delivered by an older build")
        self._session(event_id=str(message["id"]), sequence=int(message["seq"]))

        access = memory_room_access_services()
        controller = RoomRealtimeController(
            self.root,
            **access.controller_kwargs(),
            repository=self.store,
            providers=[],
        )
        try:
            diagnostics = controller.attention_active_diagnostics()[
                "provider_sync_cursor_reconciliation"
            ]
            self.assertEqual(diagnostics["repair_count"], 1)
            self.assertEqual(diagnostics["failure_count"], 0)
            self.assertEqual(
                self.store.attention_state("general", "agent-a").last_provider_sync_seq,
                message["seq"],
            )
        finally:
            controller.close()


if __name__ == "__main__":
    unittest.main()
