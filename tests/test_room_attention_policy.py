import tempfile
import unittest
from pathlib import Path

from agentsassemble.room_attention_coordinator import RoomAttentionCoordinator
from agentsassemble.room_attention_policy import (
    SHADOW_ATTENTION_SAMPLE_MODULUS,
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

    def test_active_coordinator_claims_selected_job_without_provider_work(self):
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
                content="이어서 이야기해줘",
            )

            result = coordinator.evaluate_active(
                event,
                candidate_ids=("codex",),
                eligible_ids=("codex",),
                last_spoke_sequences={"codex": 0},
                max_agent_relay_depth=2,
                owner_id="controller-a",
                lease_seconds=30,
            )

            self.assertEqual(result["job"]["outcome"], "selected")
            self.assertEqual(result["job"]["status"], "pending")
            self.assertEqual(result["lease"]["status"], "active")
            self.assertEqual(
                repository.attention_jobs("general", mode="active")[0]["status"],
                "leased",
            )
            self.assertEqual(repository.session("general", "codex"), {})


if __name__ == "__main__":
    unittest.main()
