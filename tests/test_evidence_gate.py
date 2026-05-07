import unittest

from agentsassemble.evidence import apply_evidence_gate
from agentsassemble.models import get_research_depth


class EvidenceGateTests(unittest.TestCase):
    def test_unsupported_claims_are_removed_from_supported_evidence(self):
        research = {
            "sources": [{"url": "https://example.com/source-1"}],
            "confidence": "high",
            "claim_evidence": [
                {
                    "claim": "supported",
                    "evidence": ["https://example.com/source-1"],
                    "confidence": "high",
                },
                {
                    "claim": "missing source",
                    "evidence": ["https://example.com/source-404"],
                    "confidence": "high",
                },
                {
                    "claim": "no evidence",
                    "evidence": [],
                    "confidence": "high",
                },
            ],
            "counterclaims": [],
        }

        gated = apply_evidence_gate(research, get_research_depth("smoke"))

        self.assertEqual([claim["claim"] for claim in gated["claim_evidence"]], ["supported"])
        self.assertEqual(len(gated["unsupported_claims"]), 2)
        self.assertEqual(gated["evidence_gate"]["status"], "warn")
        self.assertEqual(gated["confidence"], "low")


if __name__ == "__main__":
    unittest.main()
