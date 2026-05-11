import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.meeting import run_demo_meeting


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


if __name__ == "__main__":
    unittest.main()
