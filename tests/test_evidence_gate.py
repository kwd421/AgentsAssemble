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

    def test_explicit_weak_relation_is_separated_from_supported_claims(self):
        research = _baseline_research()
        research["claim_evidence"].append(
            {
                "claim": "weakly framed claim",
                "evidence": ["https://example.com/source-4"],
                "confidence": "high",
                "evidence_relation": "weak",
            }
        )

        gated = apply_evidence_gate(research, get_research_depth("smoke"))

        self.assertEqual(gated["evidence_gate"]["status"], "warn")
        self.assertEqual(gated["confidence"], "medium")
        self.assertEqual(len(gated["claim_evidence"]), 3)
        self.assertEqual(len(gated["weak_claims"]), 1)
        self.assertEqual(gated["evidence_gate"]["weak_claim_count"], 1)

    def test_explicit_contradiction_is_verifier_rejected(self):
        research = _baseline_research()
        research["claim_evidence"].append(
            {
                "claim": "contradicted claim",
                "evidence": ["https://example.com/source-4"],
                "confidence": "high",
                "evidence_relation": "contradicts",
            }
        )

        gated = apply_evidence_gate(research, get_research_depth("smoke"))

        self.assertEqual(gated["evidence_gate"]["status"], "warn")
        self.assertEqual(gated["confidence"], "low")
        self.assertEqual(len(gated["claim_evidence"]), 3)
        self.assertEqual(len(gated["verifier_rejected_claims"]), 1)
        self.assertEqual(gated["evidence_gate"]["verifier_rejected_claim_count"], 1)

    def test_missing_explicit_relation_still_supports_when_url_exists(self):
        research = _baseline_research()

        gated = apply_evidence_gate(research, get_research_depth("smoke"))

        self.assertEqual(gated["evidence_gate"]["status"], "pass")
        self.assertEqual(gated["confidence"], "high")
        self.assertEqual(len(gated["claim_evidence"]), 3)
        self.assertEqual(len(gated["claim_verification"]), 3)
        self.assertEqual({record["verdict"] for record in gated["claim_verification"]}, {"supports"})


def _baseline_research() -> dict:
    return {
        "sources": [
            {"url": f"https://example.com/source-{index}", "quality": "official"}
            for index in range(1, 6)
        ],
        "confidence": "high",
        "claim_evidence": [
            {
                "claim": f"supported {index}",
                "evidence": [f"https://example.com/source-{index}"],
                "confidence": "high",
            }
            for index in range(1, 4)
        ],
        "counterclaims": [
            {
                "claim": "counterclaim",
                "evidence": ["https://example.com/source-5"],
                "confidence": "medium",
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
