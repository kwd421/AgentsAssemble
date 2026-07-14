import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.room_attention_coordinator import RoomAttentionCoordinator
from agentsassemble.room_attention_policy import (
    SHADOW_ATTENTION_SAMPLE_MODULUS,
    ambient_trigger_rejection_reason,
    evaluate_ambient_attention,
    evaluate_attention,
    normalize_shadow_attention_mode,
    should_record_shadow_attention,
)
from agentsassemble.room_store import RoomStore


def _event(content, *, actor_id="human", actor_type="human", **fields):
    return {
        "id": fields.pop("id", "event-1"),
        "seq": fields.pop("seq", 1),
        "room_id": "general",
        "type": "message_final",
        "actor": {"participant_id": actor_id, "participant_type": actor_type},
        "content": content,
        **fields,
    }


class RoomAttentionPolicyTests(unittest.TestCase):
    def test_shadow_attention_mode_defaults_off_and_rejects_unknown_values(self):
        self.assertEqual(normalize_shadow_attention_mode(None), "off")
        self.assertEqual(normalize_shadow_attention_mode("FULL"), "full")
        with self.assertRaisesRegex(ValueError, "Unsupported attention shadow mode"):
            normalize_shadow_attention_mode("sometimes")

    def test_shadow_attention_sampling_uses_canonical_sequence_modulus(self):
        sampled_seq = SHADOW_ATTENTION_SAMPLE_MODULUS * 3

        self.assertFalse(should_record_shadow_attention({"seq": sampled_seq}, "off"))
        self.assertTrue(should_record_shadow_attention({"seq": 1}, "full"))
        self.assertTrue(should_record_shadow_attention({"seq": sampled_seq}, "sample"))
        self.assertFalse(should_record_shadow_attention({"seq": sampled_seq - 1}, "sample"))
        self.assertFalse(should_record_shadow_attention({"seq": 0}, "sample"))

    def test_ambient_selects_one_fair_speaker_for_plain_human_message(self):
        decision = evaluate_ambient_attention(
            _event("이 주제로 자유롭게 이야기해봐"),
            candidate_ids=("codex", "grok"),
            eligible_ids=("codex", "grok"),
            last_spoke_sequences={"codex": 8, "grok": 2},
            max_agent_relay_depth=2,
        )

        self.assertEqual(decision.outcome, "selected")
        self.assertEqual(decision.selected_participant_id, "grok")
        self.assertIn("ambient_human_message", decision.reasons)

    def test_ambient_trigger_rejects_votes_system_events_empty_text_and_media_only(self):
        rejected = {
            "vote": _event("", message_kind="vote"),
            "system": _event("maintenance", message_kind="system"),
            "system_actor": _event("maintenance", actor_id="system", actor_type="system"),
            "empty": _event(""),
            "media": _event("", attachments=[{"id": "attachment-1"}]),
            "lifecycle": {**_event("joined"), "type": "participant_joined"},
        }

        self.assertEqual(ambient_trigger_rejection_reason(rejected["vote"]), "ambient_vote_event")
        self.assertEqual(
            ambient_trigger_rejection_reason(rejected["system"]),
            "ambient_message_kind_not_supported",
        )
        self.assertEqual(
            ambient_trigger_rejection_reason(rejected["system_actor"]),
            "ambient_actor_not_trusted",
        )
        self.assertEqual(ambient_trigger_rejection_reason(rejected["empty"]), "ambient_empty_content")
        self.assertEqual(
            ambient_trigger_rejection_reason(rejected["media"]),
            "ambient_unsupported_media_only",
        )
        self.assertEqual(
            ambient_trigger_rejection_reason(rejected["lifecycle"]),
            "ambient_event_type_not_supported",
        )
        for event in rejected.values():
            decision = evaluate_ambient_attention(
                event,
                candidate_ids=("codex",),
                eligible_ids=("codex",),
                last_spoke_sequences={"codex": 0},
                max_agent_relay_depth=2,
            )
            self.assertEqual(decision.outcome, "silent")

    def test_ambient_accepts_explicit_question_reply_and_trusted_internal_trigger(self):
        events = (
            _event("누가 먼저 볼래?"),
            _event("이어갈게", reply_to_participant_id="codex"),
            _event(
                "예약된 후속 논의를 시작합니다.",
                actor_id="room-scheduler",
                actor_type="service",
                metadata={"trusted_ambient_trigger": True},
            ),
        )

        for event in events:
            self.assertEqual(ambient_trigger_rejection_reason(event), "")
            decision = evaluate_ambient_attention(
                event,
                candidate_ids=("codex",),
                eligible_ids=("codex",),
                last_spoke_sequences={"codex": 0},
                max_agent_relay_depth=2,
            )
            self.assertEqual(decision.outcome, "selected")

    def test_ambient_agent_handoff_stops_at_chain_budget(self):
        first = evaluate_ambient_attention(
            _event("내 의견은 이래.", actor_id="codex", actor_type="agent", relay_depth=1),
            candidate_ids=("codex", "grok"),
            eligible_ids=("codex", "grok"),
            last_spoke_sequences={"codex": 0, "grok": 1},
            max_agent_relay_depth=2,
        )
        exhausted = evaluate_ambient_attention(
            _event(
                "이제 네 생각은?",
                actor_id="codex",
                actor_type="agent",
                id="event-2",
                seq=2,
                relay_depth=2,
            ),
            candidate_ids=("codex", "grok"),
            eligible_ids=("grok",),
            last_spoke_sequences={"grok": 0},
            max_agent_relay_depth=2,
        )

        self.assertEqual(first.selected_participant_id, "grok")
        self.assertIn("ambient_agent_handoff", first.reasons)
        self.assertEqual(exhausted.outcome, "silent")
        self.assertEqual(exhausted.reasons, ("agent_chain_budget_exhausted",))

    def test_ambient_does_not_replace_unavailable_explicit_target(self):
        decision = evaluate_ambient_attention(
            _event("@codex 답해줘"),
            candidate_ids=("codex", "grok"),
            eligible_ids=("grok",),
            last_spoke_sequences={"grok": 0},
            max_agent_relay_depth=2,
        )

        self.assertEqual(decision.outcome, "silent")
        self.assertIn("explicit_target_unavailable", decision.reasons)

    def test_direct_mention_selects_one_available_agent(self):
        decision = evaluate_attention(
            _event("@codex 이건 어떻게 봐?"),
            candidate_ids=("codex", "grok"),
            eligible_ids=("codex", "grok"),
        )

        self.assertEqual(decision.outcome, "selected")
        self.assertEqual(decision.selected_participant_id, "codex")
        self.assertIn("direct_mention", decision.reasons)

    def test_multiple_mentions_and_room_question_are_eligible_not_forced(self):
        mentions = evaluate_attention(
            _event("@codex @grok 둘은 어떻게 생각해?"),
            candidate_ids=("codex", "grok"),
            eligible_ids=("codex", "grok"),
        )
        question = evaluate_attention(
            _event("누가 먼저 살펴볼래?", id="event-2", seq=2),
            candidate_ids=("codex", "grok"),
            eligible_ids=("codex", "grok"),
        )

        self.assertEqual(mentions.outcome, "eligible")
        self.assertEqual(mentions.eligible_participant_ids, ("codex", "grok"))
        self.assertEqual(question.outcome, "eligible")
        self.assertEqual(question.reasons, ("room_question",))

    def test_unavailable_target_and_unsignalled_agent_message_stay_silent(self):
        unavailable = evaluate_attention(
            _event("@codex 답해줘"),
            candidate_ids=("codex",),
            eligible_ids=(),
        )
        agent_message = evaluate_attention(
            _event("내 의견은 이래.", actor_id="codex", actor_type="agent", id="event-2", seq=2),
            candidate_ids=("codex", "grok"),
            eligible_ids=("grok",),
        )

        self.assertEqual(unavailable.outcome, "silent")
        self.assertIn("explicit_target_unavailable", unavailable.reasons)
        self.assertEqual(agent_message.outcome, "silent")
        self.assertEqual(agent_message.reasons, ("agent_message_no_direct_signal",))

    def test_shadow_coordinator_persists_decision_without_provider_work(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = RoomStore(Path(temp_dir))
            repository.create_room("general")
            repository.upsert_participant(
                "general",
                {
                    "participant_id": "codex",
                    "display_name": "Codex",
                    "participant_type": "agent",
                },
            )
            coordinator = RoomAttentionCoordinator(repository)
            event = repository.append_event(
                "general",
                "message_final",
                actor_id="human",
                actor_type="human",
                content="@codex 확인해줘",
            )

            job = coordinator.evaluate_shadow(
                event,
                candidate_ids=("codex",),
                eligible_ids=("codex",),
            )

            self.assertEqual(job["outcome"], "selected")
            self.assertEqual(repository.attention_state("general", "codex").last_attention_evaluated_seq, event["seq"])
            self.assertEqual(repository.session("general", "codex"), {})

    def test_active_coordinator_claims_and_queues_selected_job_atomically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = RoomStore(Path(temp_dir))
            repository.create_room("general")
            repository.upsert_participant(
                "general",
                {
                    "participant_id": "codex",
                    "display_name": "Codex",
                    "participant_type": "agent",
                },
            )
            repository.upsert_session(
                "general",
                {
                    "session_id": "codex",
                    "participant_id": "codex",
                    "status": "attached",
                    "runtime_status": "idle",
                    "pending_event_ids": [],
                },
            )
            coordinator = RoomAttentionCoordinator(repository)
            event = repository.append_event(
                "general",
                "message_final",
                actor_id="human",
                actor_type="human",
                content="이어서 이야기해줘",
            )

            result = coordinator.evaluate_and_queue_active(
                event,
                candidate_ids=("codex",),
                eligible_ids=("codex",),
                last_spoke_sequences={"codex": 0},
                max_agent_relay_depth=2,
                owner_id="controller-a",
                lease_seconds=30,
                relay_depth=1,
            )

            self.assertEqual(result["job"]["outcome"], "selected")
            self.assertEqual(result["job"]["status"], "pending")
            self.assertEqual(result["lease"]["status"], "active")
            self.assertEqual(
                repository.attention_jobs("general", mode="active")[0]["status"],
                "leased",
            )
            session = repository.session("general", "codex")
            self.assertEqual(session["pending_event_ids"], [event["id"]])
            self.assertEqual(session["pending_attention_job_id"], result["job"]["job_id"])
            self.assertEqual(session["pending_attention_lease_id"], result["lease"]["lease_id"])
            self.assertEqual(session["pending_attention_source_event_id"], event["id"])
            self.assertEqual(session["pending_relay_depth"], 1)

    def test_active_coordinator_rolls_back_job_lease_cursor_and_queue_together(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = RoomStore(Path(temp_dir))
            repository.create_room("general")
            repository.upsert_participant(
                "general",
                {
                    "participant_id": "codex",
                    "display_name": "Codex",
                    "participant_type": "agent",
                },
            )
            repository.upsert_session(
                "general",
                {
                    "session_id": "codex",
                    "participant_id": "codex",
                    "status": "attached",
                    "runtime_status": "idle",
                    "pending_event_ids": [],
                },
            )
            coordinator = RoomAttentionCoordinator(repository)
            event = repository.append_event(
                "general",
                "message_final",
                actor_id="human",
                actor_type="human",
                content="이어서 이야기해줘",
            )

            with patch.object(
                repository,
                "_update_session_fields",
                side_effect=RuntimeError("injected session queue failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected session queue failure"):
                    coordinator.evaluate_and_queue_active(
                        event,
                        candidate_ids=("codex",),
                        eligible_ids=("codex",),
                        last_spoke_sequences={"codex": 0},
                        max_agent_relay_depth=2,
                        owner_id="controller-a",
                        lease_seconds=30,
                        relay_depth=1,
                    )

            self.assertEqual(repository.attention_jobs("general", mode="active"), [])
            self.assertEqual(
                repository.attention_state("general", "codex").last_attention_evaluated_seq,
                0,
            )
            self.assertEqual(repository.session("general", "codex")["pending_event_ids"], [])


if __name__ == "__main__":
    unittest.main()
