import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.live_agent_finalization import finalize_live_agent_meeting
from agentsassemble.meeting_events import append_live_event, write_live_state


def _resident_live_meeting() -> dict[str, object]:
    return {
        "meeting_id": "resident-m1",
        "question": "How should the live session finish?",
        "display_question": "How should the live session finish?",
        "topic": "resident finalization",
        "display_topic": "resident finalization",
        "meeting_mode": "debate",
        "moderator": {"enabled": True},
        "roles": [
            {
                "id": "architect",
                "display_name": "Architect",
                "lens": "system shape",
                "research_focus": "stable handoff",
                "personality": {},
                "source_preferences": [],
            },
            {
                "id": "critic",
                "display_name": "Critic",
                "lens": "risk",
                "research_focus": "failure modes",
                "personality": {},
                "source_preferences": [],
            },
        ],
        "meeting_template": {
            "id": "resident_live_v0",
            "display_name": "Resident live",
            "rounds": [
                {
                    "id": "round_1",
                    "title": "Round 1",
                    "context_scope": "meeting",
                    "instruction": "Answer from your role.",
                    "turn_control": {"selection": "all_roles"},
                }
            ],
        },
        "research_depth": {"name": "resident_live"},
        "research_steering": {"prompt": None},
        "memory_context": {"recent_episodes": [], "agent_memories": {}},
        "memory_input": {"research_summaries": []},
        "agent_bindings": [
            {"role_id": "architect", "agent_id": "agent-a", "provider_id": "local-cli"},
            {"role_id": "critic", "agent_id": "agent-b", "provider_id": "local-cli"},
        ],
        "provider_configs": {"local-cli": {"kind": "local_cli", "display_name": "Local CLI"}},
        "permission_profiles": {},
        "agent_config_source": "test",
        "debate_rounds": [],
        "room_chat": [],
        "moderator_synthesis": {},
        "decision_gate": {},
        "artifacts": {"agenda": "agenda.md"},
        "live_status": "running",
    }


class LiveAgentFinalizationTests(unittest.TestCase):
    def test_finalize_live_agent_meeting_writes_public_artifacts_from_official_live_replies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, _resident_live_meeting())
            first_request = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "resident-m1",
                    "target_agent_id": "agent-a",
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "private architect prompt",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "resident-m1",
                    "actor_id": "agent-a",
                    "target_agent_id": "agent-a",
                    "source_event_id": first_request["id"],
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "Architect official answer.",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )
            second_request = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "resident-m1",
                    "target_agent_id": "agent-b",
                    "role_id": "critic",
                    "display_name": "Critic",
                    "content": "private critic prompt",
                    "turn_id": "round_1:1:critic",
                    "turn_index": 1,
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "resident-m1",
                    "actor_id": "agent-b",
                    "target_agent_id": "agent-b",
                    "source_event_id": second_request["id"],
                    "role_id": "critic",
                    "display_name": "Critic",
                    "content": "Critic official answer.",
                    "turn_id": "round_1:1:critic",
                    "turn_index": 1,
                },
            )

            result = finalize_live_agent_meeting(meeting_dir)

            self.assertEqual(result["status"], "finalized")
            self.assertEqual(result["meeting_id"], "resident-m1")
            self.assertEqual(result["official_event_count"], 2)
            self.assertTrue((meeting_dir / "transcript.md").exists())
            self.assertTrue((meeting_dir / "decision.md").exists())
            self.assertTrue((meeting_dir / "tasks" / "architect.md").exists())
            self.assertTrue((meeting_dir / "delegate_packets" / "critic.json").exists())
            self.assertTrue((meeting_dir / "return_packets" / "critic.md").exists())

            transcript = (meeting_dir / "transcript.md").read_text(encoding="utf-8")
            self.assertIn("Architect official answer.", transcript)
            self.assertIn("Critic official answer.", transcript)
            self.assertNotIn("private architect prompt", transcript)
            self.assertNotIn("private critic prompt", transcript)

            decision = (meeting_dir / "decision.md").read_text(encoding="utf-8")
            self.assertIn("Status: needs_user_decision", decision)
            self.assertIn("Winner: Undetermined", decision)

            meeting = json.loads((meeting_dir / "meeting.json").read_text(encoding="utf-8"))
            live_state = json.loads((meeting_dir / "live_state.json").read_text(encoding="utf-8"))
            self.assertEqual(meeting["live_status"], "complete")
            self.assertEqual(meeting["live_finalization"]["status"], "finalized")
            self.assertEqual(live_state["live_status"], "complete")
            self.assertEqual(live_state["live_finalization"]["official_event_count"], 2)
            self.assertEqual(meeting["debate_rounds"][0]["status"], "answered")
            self.assertEqual(
                [message["role_id"] for message in meeting["debate_rounds"][0]["messages"]],
                ["architect", "critic"],
            )

    def test_finalize_live_agent_meeting_refuses_pending_turn_requests(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, _resident_live_meeting())
            request_event = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "resident-m1",
                    "target_agent_id": "agent-a",
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "unanswered prompt",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )

            with self.assertRaisesRegex(ValueError, request_event["id"]):
                finalize_live_agent_meeting(meeting_dir)

            self.assertFalse((meeting_dir / "decision.md").exists())
            self.assertFalse((meeting_dir / "meeting.json").exists())

    def test_finalize_live_agent_meeting_refuses_legacy_nonofficial_reply_as_pending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, _resident_live_meeting())
            request_event = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "resident-m1",
                    "target_agent_id": "agent-a",
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "private prompt",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )
            legacy_reply = {
                "id": "legacy-reply",
                "kind": "message",
                "meeting_id": "resident-m1",
                "actor_id": "agent-a",
                "source_event_id": request_event["id"],
                "role_id": "architect",
                "display_name": "Architect",
                "content": "legacy reply without official metadata",
            }
            with (meeting_dir / "live_events.jsonl").open("a", encoding="utf-8") as file:
                file.write(json.dumps(legacy_reply, ensure_ascii=False, sort_keys=True) + "\n")

            with self.assertRaisesRegex(ValueError, request_event["id"]):
                finalize_live_agent_meeting(meeting_dir)

            self.assertFalse((meeting_dir / "decision.md").exists())

    def test_finalize_live_agent_meeting_repairs_partial_final_artifacts_without_force(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            meeting = _resident_live_meeting()
            write_live_state(meeting_dir, meeting)
            (meeting_dir / "meeting.json").write_text(
                json.dumps({**meeting, "live_status": "running"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (meeting_dir / "transcript.md").write_text("# Partial Transcript\n", encoding="utf-8")
            (meeting_dir / "decision.md").write_text("# Partial Decision\n", encoding="utf-8")
            request_event = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "resident-m1",
                    "target_agent_id": "agent-a",
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "private prompt",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "resident-m1",
                    "actor_id": "agent-a",
                    "target_agent_id": "agent-a",
                    "source_event_id": request_event["id"],
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "Recovered official answer.",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )

            result = finalize_live_agent_meeting(meeting_dir)

            self.assertEqual(result["status"], "finalized")
            meeting_json = json.loads((meeting_dir / "meeting.json").read_text(encoding="utf-8"))
            live_state = json.loads((meeting_dir / "live_state.json").read_text(encoding="utf-8"))
            self.assertEqual(meeting_json["live_status"], "complete")
            self.assertEqual(live_state["live_status"], "complete")
            self.assertIn("Recovered official answer.", (meeting_dir / "transcript.md").read_text(encoding="utf-8"))
            self.assertTrue((meeting_dir / "tasks" / "architect.md").exists())
            self.assertTrue((meeting_dir / "delegate_packets" / "architect.json").exists())
            self.assertTrue((meeting_dir / "return_packets" / "architect.json").exists())

    def test_finalize_live_agent_meeting_refuses_new_pending_request_after_finalization(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, _resident_live_meeting())
            first_request = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "resident-m1",
                    "target_agent_id": "agent-a",
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "private prompt",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "resident-m1",
                    "actor_id": "agent-a",
                    "target_agent_id": "agent-a",
                    "source_event_id": first_request["id"],
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "Initial official answer.",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )
            self.assertEqual(finalize_live_agent_meeting(meeting_dir)["status"], "finalized")
            pending_request = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "resident-m1",
                    "target_agent_id": "agent-b",
                    "role_id": "critic",
                    "display_name": "Critic",
                    "content": "new private prompt",
                    "turn_id": "round_1:1:critic",
                    "turn_index": 1,
                },
            )

            with self.assertRaisesRegex(ValueError, pending_request["id"]):
                finalize_live_agent_meeting(meeting_dir)

    def test_finalize_live_agent_meeting_reads_full_live_event_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, _resident_live_meeting())
            request_event = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "resident-m1",
                    "target_agent_id": "agent-a",
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "old private prompt",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "resident-m1",
                    "actor_id": "agent-a",
                    "target_agent_id": "agent-a",
                    "source_event_id": request_event["id"],
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "Old official answer beyond tail.",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )
            for index in range(210):
                append_live_event(
                    meeting_dir,
                    {
                        "kind": "status",
                        "meeting_id": "resident-m1",
                        "content": f"status {index}",
                    },
                )

            result = finalize_live_agent_meeting(meeting_dir)

            self.assertEqual(result["official_event_count"], 1)
            transcript = (meeting_dir / "transcript.md").read_text(encoding="utf-8")
            self.assertIn("Old official answer beyond tail.", transcript)
