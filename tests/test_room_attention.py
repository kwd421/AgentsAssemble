import unittest

from agentsassemble.room_attention import AgentAttentionState, AttentionEvaluation


class AgentAttentionStateTests(unittest.TestCase):
    def test_cursors_advance_independently(self):
        state = AgentAttentionState(room_id="general", participant_id="agent-a")

        observed = state.advance(observed_seq=12)
        evaluated = observed.advance(attention_evaluated_seq=10)
        synced = evaluated.advance(provider_sync_seq=7, spoke_seq=6)

        self.assertEqual(synced.last_observed_seq, 12)
        self.assertEqual(synced.last_attention_evaluated_seq, 10)
        self.assertEqual(synced.last_provider_sync_seq, 7)
        self.assertEqual(synced.last_spoke_seq, 6)

    def test_cursor_cannot_move_backwards(self):
        state = AgentAttentionState(
            room_id="general",
            participant_id="agent-a",
            last_observed_seq=12,
        )

        with self.assertRaisesRegex(ValueError, "cannot move backwards"):
            state.advance(observed_seq=11)


class AttentionEvaluationTests(unittest.TestCase):
    def test_selected_eligible_and_silent_outcomes_have_distinct_invariants(self):
        selected = AttentionEvaluation(
            room_id="general",
            source_event_id="event-1",
            source_seq=1,
            outcome="selected",
            selected_participant_id="agent-a",
            eligible_participant_ids=("agent-a", "agent-b"),
            reasons=("direct_mention",),
        )
        eligible = AttentionEvaluation(
            room_id="general",
            source_event_id="event-2",
            source_seq=2,
            outcome="eligible",
            eligible_participant_ids=("agent-a", "agent-b"),
            reasons=("room_question",),
        )
        silent = AttentionEvaluation(
            room_id="general",
            source_event_id="event-3",
            source_seq=3,
            outcome="silent",
            reasons=("no_attention_signal",),
        )

        self.assertEqual(selected.selected_participant_id, "agent-a")
        self.assertEqual(eligible.eligible_participant_ids, ("agent-a", "agent-b"))
        self.assertEqual(silent.eligible_participant_ids, ())

    def test_invalid_outcome_shapes_are_rejected(self):
        invalid_payloads = (
            {"outcome": "selected"},
            {"outcome": "eligible"},
            {"outcome": "silent", "eligible_participant_ids": ("agent-a",)},
            {"outcome": "eligible", "selected_participant_id": "agent-a", "eligible_participant_ids": ("agent-a",)},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                AttentionEvaluation(
                    room_id="general",
                    source_event_id="event-1",
                    source_seq=1,
                    **payload,
                )


if __name__ == "__main__":
    unittest.main()
