import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

from agentsassemble.providers.launch_specs import NativeCliProviderSpec
from agentsassemble.room_attention import AttentionEvaluation
from agentsassemble.room_database import open_room_database
from agentsassemble.room_errors import RoomCommandRejected
from agentsassemble.room_event_broker import RoomEventBroker
from agentsassemble.room_store import RoomStore
from agentsassemble.room_turn_coordinator import RoomTurnCoordinator


class RoomTurnCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = RoomStore(self.root)
        self.store.create_room("general", label="General")
        self.store.upsert_participant(
            "general",
            {
                "participant_id": "codex",
                "display_name": "Codex",
                "role": "agent",
                "status": "joined",
            },
        )
        self.store.upsert_session(
            "general",
            {
                "session_id": "codex",
                "participant_id": "codex",
                "display_name": "Codex",
                "status": "attached",
                "runtime_status": "idle",
                "enabled": True,
                "model": "fixture-model",
                "requested_model_id": "fixture-model",
                "model_selection_kind": "exact",
                "model_observation_policy": "unavailable",
                "pending_event_ids": [],
                "inflight_event_ids": [],
                "last_provider_sync_seq": 0,
                "turn_count": 0,
            },
        )
        self.spec = NativeCliProviderSpec(
            agent_id="codex",
            display_name="Codex",
            command=("codex",),
            cwd=str(self.root),
            turn_timeout_seconds=12.0,
        )
        self.broker = RoomEventBroker()
        self.identity = {
            "agent_id": "codex",
            "session_id": "codex",
            "client_type": "agent_bridge",
            "meeting_id": "general",
        }
        self.channel = self.broker.connect(self.identity)
        generation = self.broker.activate_bridge(self.channel)
        self.identity["bridge_generation"] = generation
        self.store.update_session_fields("general", "codex", bridge_generation=generation)
        self.packet: dict[str, object] = {}
        self.published: list[dict[str, object]] = []
        self.coordinator = RoomTurnCoordinator(
            self.root,
            store=self.store,
            broker=self.broker,
            lock=threading.RLock(),
            provider_lookup=lambda _room_id, _agent_id: self.spec,
            ensure_room=lambda room_id: self.store.create_room(room_id),
            publish_session_state=lambda _room_id, session: self.published.append(dict(session)),
            is_closed=lambda: False,
            recovery_delay_seconds=0.1,
            recovery_scheduler=lambda _delay, _callback: None,
            packet_builder=lambda *_args, **_kwargs: dict(self.packet),
        )

    def tearDown(self):
        self.broker.close()
        self.temp.cleanup()

    def _message(self, content):
        return self.store.append_event(
            "general",
            "message_final",
            participant_id="operator-local",
            actor_id="operator-local",
            actor_type="human",
            content=content,
        )

    def _set_packet(self, event):
        self.packet = {
            "events": [event],
            "provider_input": f"Host: {event['content']}",
            "provider_visible_chars": len(str(event["content"])),
            "provider_visible_event_count": 1,
            "input_mode": "incremental",
            "last_provider_sync_event_id_after": event["id"],
            "last_provider_sync_seq_before": 0,
            "last_provider_sync_seq_after": event["seq"],
            "provider_context_after_seq": 0,
        }

    def test_assignment_keeps_active_inflight_and_sync_boundaries_together(self):
        included = self._message("included")
        deferred = self._message("deferred")
        self._set_packet(included)
        self.store.update_session_fields(
            "general",
            "codex",
            pending_event_ids=[included["id"], deferred["id"]],
            pending_relay_depth=1,
        )

        assigned = self.coordinator.assign_pending("general", "codex")

        self.assertTrue(assigned)
        session = self.store.session("general", "codex")
        self.assertEqual(session["runtime_status"], "busy")
        self.assertEqual(session["turn_phase"], "thinking")
        self.assertTrue(session["active_turn_id"])
        self.assertEqual(session["active_source_event_id"], included["id"])
        self.assertEqual(session["inflight_event_ids"], [included["id"]])
        self.assertEqual(session["pending_event_ids"], [deferred["id"]])
        self.assertEqual(session["input_up_to_event_id"], included["id"])
        self.assertEqual(session["input_up_to_seq"], included["seq"])
        assignment = next(message for message in self.channel.drain() if message.get("op") == "turn.assign")
        self.assertEqual(assignment["turn_id"], session["active_turn_id"])
        self.assertEqual(assignment["provider_context_event_ids"], [included["id"]])

    def test_delivery_failure_preserves_relay_depth_for_retry(self):
        source = self._message("relay source")
        self._set_packet(source)
        self.store.update_session_fields(
            "general",
            "codex",
            pending_event_ids=[source["id"]],
            pending_relay_depth=2,
        )
        self.broker.disconnect(self.channel)

        self.assertFalse(self.coordinator.assign_pending("general", "codex"))

        session = self.store.session("general", "codex")
        self.assertEqual(session["pending_event_ids"], [source["id"]])
        self.assertEqual(session["pending_relay_depth"], 2)
        self.assertEqual(session.get("active_relay_depth", 0), 0)

    def test_final_message_advances_cursor_and_clears_active_turn_atomically(self):
        source = self._message("source")
        self._set_packet(source)
        self.store.update_session_fields("general", "codex", pending_event_ids=[source["id"]])
        self.assertTrue(self.coordinator.assign_pending("general", "codex"))
        turn_id = str(self.store.session("general", "codex")["active_turn_id"])

        result = self.coordinator.message_final(
            self.identity,
            "general",
            {"turn_id": turn_id, "content": "final answer", "latency": {"ttfo_ms": 25}},
        )

        session = self.store.session("general", "codex")
        self.assertEqual(result["event"]["content"], "final answer")
        self.assertEqual(session["runtime_status"], "idle")
        self.assertEqual(session["active_turn_id"], "")
        self.assertEqual(session["turn_phase"], "")
        self.assertEqual(session["inflight_event_ids"], [])
        self.assertEqual(session["last_provider_sync_event_id"], source["id"])
        self.assertEqual(session["last_provider_sync_seq"], source["seq"])
        self.assertEqual(session["last_seen_seq"], source["seq"])
        self.assertEqual(session["turn_count"], 1)

    def test_stale_generation_and_invalid_phase_are_rejected_before_state_change(self):
        source = self._message("source")
        self._set_packet(source)
        self.store.update_session_fields("general", "codex", pending_event_ids=[source["id"]])
        self.assertTrue(self.coordinator.assign_pending("general", "codex"))
        session = self.store.session("general", "codex")
        stale_identity = {**self.identity, "bridge_generation": int(session["bridge_generation"]) + 1}

        with self.assertRaises(RoomCommandRejected) as stale:
            self.coordinator.turn_state(
                stale_identity,
                "general",
                {"turn_id": session["active_turn_id"], "phase": "streaming"},
            )
        with self.assertRaises(RoomCommandRejected) as invalid:
            self.coordinator.turn_state(
                self.identity,
                "general",
                {"turn_id": session["active_turn_id"], "phase": "completed"},
            )

        self.assertEqual(stale.exception.code, "stale_bridge_generation")
        self.assertEqual(invalid.exception.code, "turn_phase_invalid")
        self.assertEqual(self.store.session("general", "codex")["turn_phase"], "thinking")

    def test_restart_expires_old_attention_lease_and_reclaims_before_assignment(self):
        source = self._message("ambient source")
        self._set_packet(source)
        evaluation = AttentionEvaluation(
            room_id="general",
            source_event_id=source["id"],
            source_seq=source["seq"],
            outcome="selected",
            selected_participant_id="codex",
            eligible_participant_ids=("codex",),
            reasons=("ambient_human_message",),
        )
        with self.store.transaction("general") as transaction:
            job = transaction.record_attention_evaluation(evaluation, mode="active", status="pending")
            old_lease = transaction.claim_attention_job(
                job["job_id"],
                participant_id="codex",
                owner_id="old-controller",
                lease_seconds=300,
            )
            transaction.update_session_fields(
                "codex",
                runtime_status="busy",
                active_turn_id="turn-before-restart",
                inflight_event_ids=[source["id"]],
                active_attention_job_id=job["job_id"],
                active_attention_lease_id=old_lease["lease_id"],
                active_attention_source_event_id=source["id"],
            )
        with closing(open_room_database(self.store.database_path)) as connection:
            connection.execute(
                "UPDATE attention_leases SET expires_at = ? WHERE lease_id = ?",
                ("2000-01-01T00:00:00+00:00", old_lease["lease_id"]),
            )

        crashed = self.store.session("general", "codex")
        fields = self.coordinator.reconcile_session_attention(
            "general",
            crashed,
            pending_event_ids=[source["id"]],
        )
        self.store.update_session_fields(
            "general",
            "codex",
            runtime_status="idle",
            active_turn_id="",
            inflight_event_ids=[],
            **fields,
        )

        self.assertEqual(
            self.store.attention_lease("general", old_lease["lease_id"])["status"],
            "expired",
        )
        self.assertEqual(fields["pending_attention_lease_id"], "")
        self.assertTrue(self.coordinator.assign_pending("general", "codex"))
        current = self.store.session("general", "codex")
        new_lease_id = str(current["active_attention_lease_id"])
        self.assertNotEqual(new_lease_id, old_lease["lease_id"])
        self.assertEqual(self.store.attention_lease("general", new_lease_id)["status"], "active")
        self.assertNotEqual(
            self.store.attention_lease("general", new_lease_id)["owner_id"],
            "old-controller",
        )

    def test_assignment_rejects_a_session_only_provider_cursor_update(self):
        source = self._message("already delivered")
        self._set_packet(source)
        self.store.update_session_fields(
            "general",
            "codex",
            last_provider_sync_event_id=source["id"],
            last_provider_sync_seq=source["seq"],
            pending_event_ids=[source["id"]],
        )

        with self.assertRaises(RoomCommandRejected) as mismatch:
            self.coordinator.assign_pending("general", "codex")

        self.assertEqual(mismatch.exception.code, "provider_sync_cursor_mismatch")


if __name__ == "__main__":
    unittest.main()
