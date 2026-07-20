import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.artifact_packets import build_return_packet
from agentsassemble.legacy.meeting.core.runner import run_demo_meeting


class DelegatePacketTests(unittest.TestCase):
    def test_meeting_writes_delegate_packets_for_each_role(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_demo_meeting(adapter_name="mock", output_root=Path(temp_dir))

            meeting = json.loads((result.meeting_dir / "meeting.json").read_text(encoding="utf-8"))
            packet_path = result.meeting_dir / "delegate_packets" / "lore_lawyer.json"
            markdown_path = result.meeting_dir / "delegate_packets" / "lore_lawyer.md"
            self.assertTrue(packet_path.exists())
            self.assertTrue(markdown_path.exists())
            packet = json.loads(packet_path.read_text(encoding="utf-8"))

        self.assertEqual(meeting["artifacts"]["delegate_packets"], "delegate_packets/")
        self.assertEqual(meeting["delegate_packets"]["lore_lawyer"]["json"], "delegate_packets/lore_lawyer.json")
        self.assertEqual(packet["meeting_id"], result.meeting_id)
        self.assertEqual(packet["role"]["id"], "lore_lawyer")
        self.assertEqual(packet["persona"]["display_name"], "설정충")
        self.assertIn("memory_summary", packet["memory"])
        self.assertIn("current_stance", packet["stance"])
        self.assertEqual(packet["permissions"]["mode"], "meeting_read_only")
        self.assertFalse(packet["permissions"]["filesystem_write"])
        self.assertEqual(packet["decision_gate"]["status"], meeting["decision_gate"]["status"])
        self.assertEqual(packet["return_schema"]["artifact"], "return_packets/lore_lawyer.json")
        self.assertEqual(packet["provenance"]["source"], "AgentsAssemble delegate packet v0")

    def test_return_packet_links_delegate_packet_for_handoff(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_demo_meeting(adapter_name="mock", output_root=Path(temp_dir))

            packet = json.loads(
                (result.meeting_dir / "return_packets" / "show_me_the_feats.json").read_text(encoding="utf-8")
            )

        self.assertEqual(packet["delegate_packet"], "delegate_packets/show_me_the_feats.json")
        self.assertIn("decision_gate", packet)
        self.assertIn("Review decision gate before acting.", packet["handoff_checklist"])
        self.assertIn("Review delegate packet before claiming continuity.", packet["handoff_checklist"])

    def test_return_packet_does_not_treat_opposing_winner_mention_as_support(self):
        meeting = {
            "meeting_id": "m1",
            "question": "question",
            "moderator_synthesis": {
                "winner": "Akainu",
                "confidence": "high",
                "summary": "Akainu wins.",
                "caveats": [],
                "tasks": {},
            },
            "decision_status": {"status": "partial", "next_actions": []},
            "decision_gate": {"status": "split_decision", "can_finalize": True, "reasons": ["minority_positions_present"]},
            "debate_rounds": [
                {
                    "title": "Round 1",
                    "messages": [
                        {
                            "role_id": "a",
                            "round": "round_1",
                            "position": "Aokiji beats Akainu head-to-head",
                            "stance_status": "held",
                            "confidence": "medium",
                            "content": "Aokiji wins this matchup.",
                        }
                    ],
                }
            ],
            "memory_input": {"research_summaries": [{"role_id": "a", "status": "complete"}]},
        }

        packet = build_return_packet(meeting, {"id": "a", "display_name": "A"})

        self.assertEqual(packet["decision"]["outcome_for_role"], "lost_or_not_selected")

    def test_return_packet_marks_role_outcome_unresolved_when_gate_cannot_finalize(self):
        meeting = {
            "meeting_id": "m1",
            "question": "question",
            "moderator_synthesis": {
                "winner": "Akainu",
                "confidence": "high",
                "summary": "Akainu wins.",
                "caveats": [],
                "tasks": {},
            },
            "decision_status": {"status": "partial", "next_actions": []},
            "decision_gate": {"status": "needs_more_research", "can_finalize": False, "reasons": ["evidence_gate:warn"]},
            "debate_rounds": [
                {
                    "title": "Round 1",
                    "messages": [
                        {
                            "role_id": "a",
                            "round": "round_1",
                            "position": "Akainu wins",
                            "stance_status": "held",
                            "confidence": "medium",
                            "content": "Akainu wins.",
                        }
                    ],
                }
            ],
            "memory_input": {"research_summaries": [{"role_id": "a", "status": "complete"}]},
        }

        packet = build_return_packet(meeting, {"id": "a", "display_name": "A"})

        self.assertEqual(packet["decision"]["outcome_for_role"], "unresolved")

    def test_return_packet_blocks_handoff_when_user_decision_is_required(self):
        meeting = {
            "meeting_id": "m1",
            "question": "question",
            "moderator_synthesis": {
                "winner": "User decision required",
                "confidence": "none",
                "summary": "Moderator is disabled.",
                "caveats": [],
                "tasks": {},
            },
            "decision_status": {"status": "pending_user", "next_actions": []},
            "decision_gate": {
                "status": "needs_user_decision",
                "can_finalize": False,
                "required_action": "user_decision",
                "reasons": ["moderator_disabled"],
            },
            "debate_rounds": [
                {
                    "title": "Round 1",
                    "messages": [
                        {
                            "role_id": "a",
                            "round": "round_1",
                            "position": "A",
                            "stance_status": "held",
                            "confidence": "medium",
                            "content": "A.",
                        }
                    ],
                }
            ],
            "memory_input": {"research_summaries": [{"role_id": "a", "status": "complete"}]},
        }

        packet = build_return_packet(meeting, {"id": "a", "display_name": "A"})

        self.assertEqual(packet["decision"]["outcome_for_role"], "unresolved")
        self.assertIn("Do not start implementation until the decision gate is resolved.", packet["handoff_checklist"])
        self.assertIn("Wait for a user decision or enable moderator synthesis before acting.", packet["handoff_checklist"])

    def test_return_packet_names_invalid_gate_recovery_action(self):
        meeting = {
            "meeting_id": "m1",
            "question": "question",
            "moderator_synthesis": {
                "winner": "Undetermined",
                "confidence": "low",
                "summary": "Fallback synthesis.",
                "caveats": [],
                "tasks": {},
            },
            "decision_status": {"status": "partial", "next_actions": []},
            "decision_gate": {
                "status": "invalid",
                "can_finalize": False,
                "required_action": "rerun_moderator_or_user_review",
                "reasons": ["moderator_fallback"],
            },
            "debate_rounds": [],
            "memory_input": {"research_summaries": [{"role_id": "a", "status": "complete"}]},
        }

        packet = build_return_packet(meeting, {"id": "a", "display_name": "A"})

        self.assertIn("Do not start implementation until the decision gate is resolved.", packet["handoff_checklist"])
        self.assertIn("Rerun moderator synthesis or request user review before acting.", packet["handoff_checklist"])


if __name__ == "__main__":
    unittest.main()
