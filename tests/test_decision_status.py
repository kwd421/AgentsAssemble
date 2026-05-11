import unittest

from agentsassemble.decision_status import derive_decision_status


class DecisionStatusTests(unittest.TestCase):
    def test_resolved_when_winner_is_confident_and_no_open_caveats(self):
        status = derive_decision_status(
            {"winner": "A", "confidence": "high", "caveats": [], "summary": "A wins."},
            {"status": "pass"},
        )

        self.assertEqual(status["status"], "resolved")
        self.assertEqual(status["next_actions"], [])

    def test_partial_when_winner_is_low_confidence_or_caveated(self):
        status = derive_decision_status(
            {"winner": "A", "confidence": "low", "caveats": ["rules are unclear"], "summary": "A maybe wins."},
            {"status": "warn"},
        )

        self.assertEqual(status["status"], "partial")
        self.assertIn("Run another round or request a user decision.", status["next_actions"])
        self.assertIn("Evidence Gate is warn; review weak or unsupported claims.", status["next_actions"])

    def test_no_consensus_when_winner_is_undetermined(self):
        status = derive_decision_status(
            {"winner": "Undetermined", "confidence": "low", "caveats": [], "summary": "No consensus."},
            {"status": "pass"},
        )

        self.assertEqual(status["status"], "no_consensus")
        self.assertIn("Ask the user to choose, add another round, or assign follow-up research.", status["next_actions"])

    def test_gate_status_drives_legacy_status_when_available(self):
        status = derive_decision_status(
            {"winner": "A", "confidence": "high", "caveats": [], "summary": "A wins."},
            {"status": "pass"},
            {"status": "blocked", "required_action": "rerun_failed_debate_round", "reasons": ["debate_failed:b:round_1"]},
        )

        self.assertEqual(status["status"], "partial")
        self.assertEqual(status["decision_gate_status"], "blocked")
        self.assertIn("Rerun failed debate turn before deciding.", status["next_actions"])


if __name__ == "__main__":
    unittest.main()
