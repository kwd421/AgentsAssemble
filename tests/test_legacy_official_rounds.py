import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.legacy.meeting.records import read_meeting_record
from agentsassemble.legacy.meeting.official_rounds import (
    LegacyOfficialRoundService,
    _live_agent_turn_rounds_payload_locked,
)
from agentsassemble.live_agent_operations import read_live_agent_operations
from agentsassemble.live_agents import connect_live_agent
from agentsassemble.legacy.meeting.core.events import write_live_state


class LegacyOfficialRoundServiceTests(unittest.TestCase):
    def test_answered_round_records_progress_and_prompt_free_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meeting_dir = self._write_meeting(root, round_ids=("round-1",))
            self._connect_agent(root)
            sequence_result = {
                "status": "answered",
                "meeting_id": "room-a",
                "turn_count": 1,
                "answered_count": 1,
                "timeout_count": 0,
                "skipped_count": 0,
                "cancelled_count": 0,
                "stopped": False,
                "stop_on_timeout": False,
                "timeout_seconds": 3.0,
                "results": [],
            }

            with patch(
                "agentsassemble.legacy.meeting.official_rounds.live_agent_turn_sequence_payload",
                return_value=sequence_result,
            ):
                result = LegacyOfficialRoundService(root).round(
                    "room-a",
                    {
                        "round_id": "round-1",
                        "content": "SECRET ROUND INSTRUCTION",
                        "timeout_seconds": 3,
                    },
                )

            meeting = read_meeting_record(meeting_dir)
            operation = read_live_agent_operations(root, operation="official_turn.round")[0]

        self.assertEqual(result["status"], "answered")
        self.assertEqual(meeting["debate_rounds"][0]["status"], "answered")
        self.assertEqual(operation["status"], "success")
        self.assertEqual(operation["details"]["round_id"], "round-1")
        self.assertNotIn("SECRET ROUND INSTRUCTION", json.dumps(operation, ensure_ascii=False))

    def test_stop_on_timeout_skips_remaining_rounds(self) -> None:
        first_result = {
            "status": "timeout",
            "meeting_id": "room-a",
            "round_id": "round-1",
            "role_ids": ["speaker"],
            "turn_count": 1,
            "answered_count": 0,
            "timeout_count": 1,
            "skipped_count": 0,
        }

        with patch(
            "agentsassemble.legacy.meeting.official_rounds.live_agent_turn_round_payload",
            return_value=first_result,
        ) as run_round:
            result = _live_agent_turn_rounds_payload_locked(
                Path("."),
                "room-a",
                ["round-1", "round-2"],
                timeout_seconds=2.0,
                stop_on_timeout=True,
                max_rounds=2,
            )

        self.assertEqual(run_round.call_count, 1)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual([item["status"] for item in result["results"]], ["timeout", "skipped"])

    def test_requested_finalization_is_explicitly_skipped_after_timeout(self) -> None:
        timeout_result = {
            "status": "timeout",
            "meeting_id": "room-a",
            "round_count": 1,
            "answered_round_count": 0,
            "completed_round_count": 0,
            "timeout_round_count": 1,
            "skipped_round_count": 0,
            "stopped_round_count": 0,
            "stopped": False,
            "timeout_seconds": 1.0,
            "max_rounds": 1,
            "results": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch(
                "agentsassemble.legacy.meeting.official_rounds.live_agent_turn_rounds_payload",
                return_value=timeout_result,
            ):
                result = LegacyOfficialRoundService(root).rounds(
                    "room-a",
                    {"finalize_after_rounds": True, "content": "SECRET BATCH INSTRUCTION"},
                )
            operation = read_live_agent_operations(root, operation="official_turn.rounds")[0]

        self.assertEqual(result["finalization"]["status"], "skipped")
        self.assertEqual(result["finalization"]["reason"], "rounds_not_ready")
        self.assertEqual(operation["status"], "degraded")
        self.assertEqual(operation["details"]["finalization_reason"], "rounds_not_ready")
        self.assertNotIn("SECRET BATCH INSTRUCTION", json.dumps(operation, ensure_ascii=False))

    def test_preset_failure_audit_keeps_instruction_out_of_operation_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(ValueError, "Meeting missing was not found"):
                LegacyOfficialRoundService(root).preset(
                    "missing",
                    {
                        "preset_id": "meme_debate_fast",
                        "content": "SECRET PRESET INSTRUCTION",
                    },
                )
            operation = read_live_agent_operations(root, operation="official_turn.preset")[0]

        self.assertEqual(operation["status"], "failed")
        self.assertEqual(operation["details"]["preset_id"], "meme_debate_fast")
        self.assertNotIn("SECRET PRESET INSTRUCTION", json.dumps(operation, ensure_ascii=False))

    @staticmethod
    def _write_meeting(root: Path, *, round_ids: tuple[str, ...]) -> Path:
        meeting_dir = root / "meetings" / "room-a"
        meeting_dir.mkdir(parents=True)
        write_live_state(
            meeting_dir,
            {
                "meeting_id": "room-a",
                "roles": [{"id": "speaker", "display_name": "Speaker"}],
                "agent_bindings": [{"role_id": "speaker", "agent_id": "agent-a"}],
                "meeting_template": {
                    "rounds": [
                        {"id": round_id, "instruction": f"Instruction for {round_id}"}
                        for round_id in round_ids
                    ]
                },
                "debate_rounds": [],
            },
        )
        return meeting_dir

    @staticmethod
    def _connect_agent(root: Path) -> None:
        connect_live_agent(
            root,
            {
                "agent_id": "agent-a",
                "display_name": "Agent A",
                "provider_kind": "local_cli",
                "connection_kind": "local_cli",
                "meeting_id": "room-a",
            },
        )


if __name__ == "__main__":
    unittest.main()
