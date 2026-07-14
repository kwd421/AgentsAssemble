import tempfile
import unittest
from pathlib import Path

from agentsassemble.room_attention import AttentionEvaluation
from agentsassemble.room_attention_reconciliation import RoomAttentionReconciler
from agentsassemble.room_database import open_room_database
from agentsassemble.room_store import RoomStore


class RoomAttentionReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = RoomStore(self.root)
        self.store.create_room("general")

    def tearDown(self):
        self.temp.cleanup()

    def _participant_and_session(self, participant_id: str):
        self.store.upsert_participant(
            "general",
            {
                "participant_id": participant_id,
                "display_name": participant_id,
                "participant_type": "agent",
            },
        )
        self.store.upsert_session(
            "general",
            {
                "session_id": participant_id,
                "participant_id": participant_id,
                "status": "attached",
                "runtime_status": "idle",
                "pending_event_ids": [],
            },
        )

    def _selected_work(self, participant_id: str, source_seq: int):
        source = self.store.append_event(
            "general",
            "message_final",
            actor_id="human",
            actor_type="human",
            content=f"source {source_seq}",
        )
        with self.store.transaction("general") as transaction:
            job = transaction.record_attention_evaluation(
                AttentionEvaluation(
                    room_id="general",
                    source_event_id=str(source["id"]),
                    source_seq=int(source["seq"]),
                    outcome="selected",
                    selected_participant_id=participant_id,
                    eligible_participant_ids=(participant_id,),
                    reasons=("ambient_human_message",),
                ),
                mode="active",
                status="pending",
            )
            lease = transaction.claim_attention_job(
                job["job_id"],
                participant_id=participant_id,
                owner_id="old-generation",
                lease_seconds=60,
            )
        return source, job, lease

    def test_repairs_missing_job_orphan_job_and_deleted_participant(self):
        self._participant_and_session("missing-job")
        self.store.update_session_fields(
            "general",
            "missing-job",
            pending_event_ids=["missing-source"],
            pending_attention_job_id="missing-attention-job",
            pending_attention_lease_id="missing-attention-lease",
            pending_attention_source_event_id="missing-source",
        )

        self._participant_and_session("orphan-job")
        _orphan_source, orphan_job, orphan_lease = self._selected_work("orphan-job", 2)

        self._participant_and_session("deleted-agent")
        deleted_source, deleted_job, deleted_lease = self._selected_work("deleted-agent", 3)
        self.store.update_session_fields(
            "general",
            "deleted-agent",
            pending_event_ids=[deleted_source["id"]],
            pending_attention_job_id=deleted_job["job_id"],
            pending_attention_lease_id=deleted_lease["lease_id"],
            pending_attention_source_event_id=deleted_source["id"],
        )
        self.store.set_participant_status("general", "deleted-agent", "kicked")

        report = RoomAttentionReconciler(self.store).reconcile()

        missing = self.store.session("general", "missing-job")
        deleted = self.store.session("general", "deleted-agent")
        self.assertEqual(missing["pending_event_ids"], [])
        self.assertEqual(missing["pending_attention_job_id"], "")
        self.assertEqual(deleted["pending_event_ids"], [])
        self.assertEqual(deleted["pending_attention_job_id"], "")
        self.assertEqual(self.store.attention_job("general", orphan_job["job_id"])["status"], "cancelled")
        self.assertEqual(self.store.attention_lease("general", orphan_lease["lease_id"])["status"], "cancelled")
        self.assertEqual(self.store.attention_job("general", deleted_job["job_id"])["status"], "cancelled")
        self.assertEqual(self.store.attention_lease("general", deleted_lease["lease_id"])["status"], "cancelled")
        codes = {repair["code"] for repair in report.repairs}
        self.assertIn("session_job_missing", codes)
        self.assertIn("orphan_job_cancelled", codes)
        self.assertIn("selected_participant_unavailable", codes)
        self.assertEqual(
            sum(
                repair["code"] == "orphan_job_cancelled"
                and repair.get("job_id") == orphan_job["job_id"]
                for repair in report.repairs
            ),
            1,
        )
        audit_events = [
            event
            for event in self.store.read_events("general")
            if event.get("type") == "attention_reconciled"
        ]
        self.assertEqual(len(audit_events), 1)
        self.assertEqual(audit_events[0]["repair_count"], report.as_dict()["repair_count"])

    def test_expired_referenced_lease_becomes_pending_without_losing_source(self):
        self._participant_and_session("agent-a")
        source, job, lease = self._selected_work("agent-a", 2)
        self.store.update_session_fields(
            "general",
            "agent-a",
            pending_event_ids=[source["id"]],
            pending_attention_job_id=job["job_id"],
            pending_attention_lease_id=lease["lease_id"],
            pending_attention_source_event_id=source["id"],
        )
        with open_room_database(self.store.database_path) as connection:
            connection.execute(
                "UPDATE attention_leases SET expires_at = ? WHERE lease_id = ?",
                ("2000-01-01T00:00:00+00:00", lease["lease_id"]),
            )

        report = RoomAttentionReconciler(self.store).reconcile()

        session = self.store.session("general", "agent-a")
        self.assertEqual(session["pending_event_ids"], [source["id"]])
        self.assertEqual(session["pending_attention_job_id"], job["job_id"])
        self.assertEqual(session["pending_attention_lease_id"], "")
        self.assertEqual(self.store.attention_job("general", job["job_id"])["status"], "pending")
        self.assertEqual(self.store.attention_lease("general", lease["lease_id"])["status"], "expired")
        self.assertIn("lease_expired", {repair["code"] for repair in report.repairs})

    def test_preserves_referenced_unexpired_lease_owned_by_another_generation(self):
        self._participant_and_session("agent-a")
        source, job, lease = self._selected_work("agent-a", 2)
        self.store.update_session_fields(
            "general",
            "agent-a",
            pending_event_ids=[source["id"]],
            pending_attention_job_id=job["job_id"],
            pending_attention_lease_id=lease["lease_id"],
            pending_attention_source_event_id=source["id"],
        )

        report = RoomAttentionReconciler(self.store).reconcile()

        session = self.store.session("general", "agent-a")
        self.assertEqual(session["pending_attention_job_id"], job["job_id"])
        self.assertEqual(session["pending_attention_lease_id"], lease["lease_id"])
        self.assertEqual(self.store.attention_job("general", job["job_id"])["status"], "leased")
        saved_lease = self.store.attention_lease("general", lease["lease_id"])
        self.assertEqual(saved_lease["status"], "active")
        self.assertEqual(saved_lease["owner_id"], "old-generation")
        self.assertEqual(report.repairs, ())

    def test_reconciliation_reports_when_the_per_room_processing_bound_is_reached(self):
        self._participant_and_session("agent-a")
        self._participant_and_session("agent-b")

        report = RoomAttentionReconciler(self.store, max_records_per_room=1).reconcile()

        self.assertTrue(report.as_dict()["truncated"])
        self.assertEqual(report.truncated_room_ids, ("general",))


if __name__ == "__main__":
    unittest.main()
