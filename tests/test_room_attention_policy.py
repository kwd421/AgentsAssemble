import tempfile
import unittest
from pathlib import Path

from agentsassemble.room_attention_coordinator import RoomAttentionCoordinator
from agentsassemble.room_attention_policy import evaluate_attention
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


if __name__ == "__main__":
    unittest.main()
