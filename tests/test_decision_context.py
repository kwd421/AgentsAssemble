import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.providers.adapters.codex import CodexAdapter
from agentsassemble.artifact_public import render_decision, render_transcript
from agentsassemble.meeting_context import build_diagnostics, build_decision_context, public_debate_rounds, public_synthesis


class DecisionContextTests(unittest.TestCase):
    def test_decision_context_excludes_adapter_metadata(self):
        research_records = [
            {
                "role_id": "lore_lawyer",
                "summary": "공식 근거상 아카이누가 가장 방어 가능하다.",
                "confidence": "medium",
                "claim_evidence": [
                    {
                        "claim": "아카이누는 쿠잔에게 승리했다.",
                        "evidence": ["https://one-piece.com/character/Sakazuki/index.html"],
                        "evidence_relation": "supports",
                        "interpretation": "공식 캐릭터 설명에 기반한다.",
                        "confidence": "high",
                    }
                ],
                "weak_claims": [{"claim": "화력이 최상위다", "reason": "간접 근거"}],
                "counterclaims": [{"claim": "쿠잔과 격차는 작다", "why_it_matters": "10일 접전"}],
                "codex": {"stderr": "must not leak"},
            }
        ]
        debate_rounds = [
            {
                "id": "round_1",
                "title": "첫 주장",
                "messages": [
                    {
                        "role_id": "lore_lawyer",
                        "display_name": "설정충",
                        "round": "round_1",
                        "content": "아카이누 우세입니다.",
                        "position": "Sakazuki / Akainu",
                        "stance_status": "held",
                        "stance_delta": "none",
                        "changed_by": [],
                        "change_reason": "",
                        "remaining_resistance": "",
                        "emotion": {"tone": "calm", "friction": 0.2},
                        "change_conditions": ["공식 반례"],
                        "confidence": "medium",
                        "codex": {
                            "command": ["codex", "exec"],
                            "stdout": "huge stdout",
                            "stderr": "huge stderr",
                            "session_id": "abc",
                            "output_last_message": "/tmp/file",
                        },
                    }
                ],
            }
        ]

        context = build_decision_context(research_records, debate_rounds, {"status": "warn"})
        encoded = json.dumps(context, ensure_ascii=False)

        self.assertIn("아카이누 우세입니다.", encoded)
        self.assertNotIn("huge stdout", encoded)
        self.assertNotIn("huge stderr", encoded)
        self.assertNotIn('"command"', encoded)
        self.assertNotIn("session_id", encoded)
        message = context["rounds"]["round_1"][0]
        self.assertEqual(message["emotion"]["tone"], "calm")
        self.assertEqual(message["stance_delta"], "none")
        self.assertLess(len(encoded), 10000)

    def test_public_debate_rounds_strip_metadata_but_diagnostics_keep_it(self):
        debate_rounds = [
            {
                "id": "round_1",
                "title": "첫 주장",
                "messages": [
                    {
                        "role_id": "lore_lawyer",
                        "display_name": "설정충",
                        "content": "아카이누 우세",
                        "codex": {"stderr": "adapter warning", "command": ["codex"]},
                    }
                ],
            }
        ]
        synthesis = {"fallback": "local_synthesis", "diagnostics": {"reason": "failed"}}

        public_rounds = public_debate_rounds(debate_rounds)
        diagnostics = build_diagnostics(debate_rounds, synthesis)
        public_encoded = json.dumps(public_rounds, ensure_ascii=False)
        diagnostic_encoded = json.dumps(diagnostics, ensure_ascii=False)

        self.assertIn("아카이누 우세", public_encoded)
        self.assertNotIn("adapter warning", public_encoded)
        self.assertNotIn("adapter warning", diagnostic_encoded)
        self.assertNotIn('"command"', diagnostic_encoded)
        self.assertEqual(diagnostics["adapter_calls"][0]["returncode"], None)
        self.assertEqual(diagnostics["meeting_status"], "degraded")

    def test_fallback_synthesis_keeps_user_summary_clean_and_diagnostics_separate(self):
        context = {
            "evidence_gate": {"status": "warn", "total_supported_claims": 6},
            "rounds": {
                "round_1": [
                    {"role_id": "a", "position": "Sakazuki / Akainu"},
                    {"role_id": "b", "position": "Akainu 우세"},
                ]
            },
        }

        synthesis = CodexAdapter._fallback_synthesis(context, "Error: Input exceeds maximum length")

        self.assertEqual(synthesis["fallback"], "local_synthesis")
        self.assertEqual(synthesis["status"], "degraded")
        self.assertNotIn("parseable JSON", synthesis["summary"])
        self.assertNotIn("Evidence Gate status", synthesis["summary"])
        self.assertNotIn("Input exceeds", synthesis["summary"])
        self.assertIn("diagnostics", synthesis)
        self.assertEqual(synthesis["diagnostics"]["reason"], "moderator_synthesis_unavailable")
        self.assertNotIn("original_output_excerpt", synthesis["diagnostics"])

    def test_public_artifacts_do_not_include_internal_fallback_diagnostics(self):
        meeting = {
            "meeting_id": "m1",
            "question": "Q",
            "debate_rounds": [
                {
                    "title": "Round 1",
                    "messages": [
                        {
                            "display_name": "설정충",
                            "position": "Akainu",
                            "stance_status": "held",
                            "change_conditions": [],
                            "content": "근거상 Akainu.",
                        }
                    ],
                }
            ],
            "moderator_synthesis": {
                "winner": "Sakazuki / Akainu",
                "ranking": ["Sakazuki / Akainu"],
                "confidence": "medium",
                "caveats": ["Codex synthesis did not return parseable JSON."],
                "summary": "반복된 입장과 근거 품질을 기준으로 아카이누가 가장 방어 가능한 결론입니다.",
                "diagnostics": {"error": "Input exceeds the maximum length"},
                "fallback": "local_synthesis",
                "status": "degraded",
            },
            "decision_status": {
                "status": "partial",
                "next_actions": ["Run another round or request a user decision."],
            },
            "evidence_gate": {"status": "warn"},
        }

        decision = render_decision(meeting)
        transcript = render_transcript(meeting)

        self.assertIn("아카이누", decision)
        self.assertIn("Decision Status", decision)
        self.assertIn("partial", decision)
        self.assertNotIn("parseable JSON", decision)
        self.assertNotIn("Input exceeds", decision)
        self.assertNotIn("parseable JSON", transcript)
        self.assertNotIn("Input exceeds", transcript)


if __name__ == "__main__":
    unittest.main()
