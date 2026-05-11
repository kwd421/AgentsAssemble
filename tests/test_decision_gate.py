import unittest

from agentsassemble.decision_gate import derive_decision_gate


def research(role_id="role", status="complete", retry_status="not_needed"):
    return {
        "role_id": role_id,
        "status": status,
        "retry": {"status": retry_status, "attempts": 1, "max_attempts": 2},
    }


def round_message(role_id, position, stance_status="held"):
    return {"role_id": role_id, "position": position, "stance_status": stance_status}


class DecisionGateTests(unittest.TestCase):
    def test_decided_when_supported_confident_and_aligned(self):
        gate = derive_decision_gate(
            {"winner": "Alpha", "confidence": "high", "caveats": [], "summary": "Alpha wins."},
            {"status": "pass", "total_unsupported_claims": 0, "total_weak_claims": 0, "total_verifier_rejected_claims": 0},
            [research("a"), research("b")],
            [{"messages": [round_message("a", "Alpha"), round_message("b", "Alpha")]}],
        )

        self.assertEqual(gate["status"], "decided")
        self.assertTrue(gate["can_finalize"])
        self.assertEqual(gate["required_action"], "write_decision")

    def test_needs_more_research_when_research_failed_or_evidence_warns(self):
        gate = derive_decision_gate(
            {"winner": "A", "confidence": "medium", "caveats": [], "summary": "A maybe wins."},
            {"status": "warn", "total_unsupported_claims": 1, "total_weak_claims": 0, "total_verifier_rejected_claims": 0},
            [research("a", status="failed", retry_status="failed")],
            [{"messages": [round_message("a", "A")]}],
        )

        self.assertEqual(gate["status"], "needs_more_research")
        self.assertFalse(gate["can_finalize"])
        self.assertIn("research_failed:a", gate["reasons"])
        self.assertIn("evidence_gate:warn", gate["reasons"])

    def test_unknown_evidence_gate_blocks_final_decision(self):
        gate = derive_decision_gate(
            {"winner": "Alpha", "confidence": "high", "caveats": [], "summary": "Alpha wins."},
            {},
            [research("a")],
            [{"messages": [round_message("a", "Alpha")]}],
        )

        self.assertEqual(gate["status"], "needs_more_research")
        self.assertIn("evidence_gate:unknown", gate["reasons"])

    def test_research_failure_takes_precedence_over_no_consensus(self):
        gate = derive_decision_gate(
            {"winner": "Undetermined", "confidence": "low", "caveats": [], "summary": "No result."},
            {"status": "warn", "total_unsupported_claims": 1},
            [research("a", status="failed", retry_status="failed")],
            [{"messages": [round_message("a", "A")]}],
        )

        self.assertEqual(gate["status"], "needs_more_research")
        self.assertIn("winner_undetermined", gate["reasons"])
        self.assertIn("research_failed:a", gate["reasons"])

    def test_no_consensus_when_winner_is_missing(self):
        gate = derive_decision_gate(
            {"winner": "Undetermined", "confidence": "low", "caveats": [], "summary": "No result."},
            {"status": "pass"},
            [research("a"), research("b")],
            [{"messages": [round_message("a", "A"), round_message("b", "B")]}],
        )

        self.assertEqual(gate["status"], "no_consensus")
        self.assertEqual(gate["required_action"], "add_round_or_user_decision")

    def test_blocked_when_winner_exists_but_confidence_is_low(self):
        gate = derive_decision_gate(
            {"winner": "Alpha", "confidence": "low", "caveats": [], "summary": "Weak result."},
            {"status": "pass"},
            [research("a"), research("b")],
            [{"messages": [round_message("a", "Alpha"), round_message("b", "Alpha")]}],
        )

        self.assertEqual(gate["status"], "blocked")
        self.assertFalse(gate["can_finalize"])
        self.assertIn("low_confidence", gate["reasons"])

    def test_invalid_when_moderator_fell_back(self):
        gate = derive_decision_gate(
            {"winner": "A", "confidence": "medium", "fallback": True, "summary": "Fallback."},
            {"status": "pass"},
            [research("a")],
            [{"messages": [round_message("a", "A")]}],
        )

        self.assertEqual(gate["status"], "invalid")
        self.assertIn("moderator_fallback", gate["reasons"])

    def test_split_decision_when_remaining_positions_disagree(self):
        gate = derive_decision_gate(
            {"winner": "A", "confidence": "medium", "caveats": ["B has unresolved counterevidence"], "summary": "A edges it."},
            {"status": "pass"},
            [research("a"), research("b")],
            [{"messages": [round_message("a", "A"), round_message("b", "B")]}],
        )

        self.assertEqual(gate["status"], "split_decision")
        self.assertTrue(gate["can_finalize"])
        self.assertIn("minority_positions", gate)

    def test_dissent_that_mentions_winner_is_not_treated_as_alignment(self):
        gate = derive_decision_gate(
            {"winner": "Akainu", "confidence": "high", "caveats": [], "summary": "Akainu wins."},
            {"status": "pass"},
            [research("a"), research("b")],
            [{"messages": [round_message("a", "Akainu wins"), round_message("b", "Aokiji beats Akainu head-to-head")]}],
        )

        self.assertEqual(gate["status"], "split_decision")
        self.assertEqual(gate["minority_positions"], [{"role_id": "b", "position": "Aokiji beats Akainu head-to-head"}])

    def test_aligned_position_can_mention_winner_after_introductory_words(self):
        gate = derive_decision_gate(
            {"winner": "Alpha", "confidence": "high", "caveats": [], "summary": "Alpha wins."},
            {"status": "pass"},
            [research("a"), research("b")],
            [{"messages": [round_message("a", "I choose Alpha"), round_message("b", "Alpha wins")]}],
        )

        self.assertEqual(gate["status"], "decided")
        self.assertEqual(gate["minority_positions"], [])

    def test_generic_token_in_multiword_winner_does_not_match_opposing_choice(self):
        gate = derive_decision_gate(
            {"winner": "Option Alpha", "confidence": "high", "caveats": [], "summary": "Option Alpha wins."},
            {"status": "pass"},
            [research("a"), research("b")],
            [{"messages": [round_message("a", "Option Alpha wins"), round_message("b", "Option Beta wins")]}],
        )

        self.assertEqual(gate["status"], "split_decision")
        self.assertEqual(gate["minority_positions"], [])
        self.assertEqual(gate["ambiguous_positions"], [{"role_id": "b", "position": "Option Beta wins"}])

    def test_explicit_opposition_to_winner_is_recorded_as_minority(self):
        gate = derive_decision_gate(
            {"winner": "Option Alpha", "confidence": "high", "caveats": [], "summary": "Option Alpha wins."},
            {"status": "pass"},
            [research("a"), research("b")],
            [{"messages": [round_message("a", "Option Alpha wins"), round_message("b", "Option Beta beats Option Alpha")]}],
        )

        self.assertEqual(gate["status"], "split_decision")
        self.assertEqual(gate["minority_positions"], [{"role_id": "b", "position": "Option Beta beats Option Alpha"}])
        self.assertEqual(gate["ambiguous_positions"], [])

    def test_failed_debate_turn_blocks_finalization(self):
        gate = derive_decision_gate(
            {"winner": "Akainu", "confidence": "high", "caveats": [], "summary": "Akainu wins."},
            {"status": "pass"},
            [research("a"), research("b")],
            [
                {
                    "id": "round_1",
                    "messages": [
                        round_message("a", "Akainu wins"),
                        {
                            "role_id": "b",
                            "round": "round_1",
                            "status": "failed",
                            "position": "",
                            "stance_status": "blocked",
                        },
                    ],
                }
            ],
        )

        self.assertEqual(gate["status"], "blocked")
        self.assertFalse(gate["can_finalize"])
        self.assertIn("debate_failed:b:round_1", gate["reasons"])
        self.assertEqual(gate["required_action"], "rerun_failed_debate_round")

    def test_failed_debate_turn_action_precedes_evidence_warning(self):
        gate = derive_decision_gate(
            {"winner": "Akainu", "confidence": "high", "caveats": [], "summary": "Akainu wins."},
            {"status": "warn", "total_unsupported_claims": 1},
            [research("a"), research("b")],
            [
                {
                    "id": "round_1",
                    "messages": [
                        round_message("a", "Akainu wins"),
                        {
                            "role_id": "b",
                            "round": "round_1",
                            "status": "failed",
                            "position": "",
                            "stance_status": "blocked",
                        },
                    ],
                }
            ],
        )

        self.assertEqual(gate["status"], "blocked")
        self.assertEqual(gate["required_action"], "rerun_failed_debate_round")
        self.assertIn("evidence_gate:warn", gate["reasons"])


if __name__ == "__main__":
    unittest.main()
