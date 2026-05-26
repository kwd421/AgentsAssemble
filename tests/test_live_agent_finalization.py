import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.live_agent_finalization import finalize_live_agent_meeting
from agentsassemble.meeting_events import append_live_event, read_live_events, write_live_state
from agentsassemble.persona_cards import PersonaCard, PersonaLoreEntry, save_persona_card


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
            self.assertTrue((meeting_dir / "shared_memory" / "rolling-summary.md").exists())
            self.assertTrue((meeting_dir / "shared_memory" / "open-questions.md").exists())
            self.assertTrue((meeting_dir / "shared_memory" / "action-items.md").exists())
            self.assertTrue((meeting_dir / "shared_memory" / "index.json").exists())
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
            shared_memory = json.loads((meeting_dir / "shared_memory" / "index.json").read_text(encoding="utf-8"))
            shared_memory_text = (meeting_dir / "shared_memory" / "rolling-summary.md").read_text(encoding="utf-8")
            self.assertEqual(meeting["live_status"], "complete")
            self.assertEqual(meeting["live_finalization"]["status"], "finalized")
            self.assertEqual(meeting["shared_memory"]["official_event_count"], 2)
            self.assertEqual(shared_memory["official_event_count"], 2)
            self.assertIn("Architect official answer.", shared_memory_text)
            self.assertIn("Critic official answer.", shared_memory_text)
            self.assertNotIn("private architect prompt", shared_memory_text)
            self.assertEqual(live_state["live_status"], "complete")
            self.assertEqual(live_state["shared_memory"]["last_official_event_id"], shared_memory["last_official_event_id"])
            self.assertEqual(live_state["live_finalization"]["official_event_count"], 2)
            self.assertEqual(meeting["debate_rounds"][0]["status"], "answered")
            self.assertEqual(
                [message["role_id"] for message in meeting["debate_rounds"][0]["messages"]],
                ["architect", "critic"],
            )
            self.assertEqual(result["return_packet_event_count"], 2)
            return_packet_events = [
                event
                for event in read_live_events(meeting_dir, limit=None)
                if event.get("kind") == "artifact" and event.get("artifact_kind") == "return_packet"
            ]
            self.assertEqual({event.get("target_agent_id") for event in return_packet_events}, {"agent-a", "agent-b"})
            self.assertEqual({event.get("official_record") for event in return_packet_events}, {False})
            self.assertEqual({event.get("channel") for event in return_packet_events}, {"system"})
            self.assertIn("return_packets/architect.md", {event.get("artifact_path") for event in return_packet_events})
            self.assertIn("return_packets/critic.md", {event.get("artifact_path") for event in return_packet_events})
            self.assertNotIn("private architect prompt", json.dumps(return_packet_events, ensure_ascii=False))
            self.assertNotIn("private critic prompt", json.dumps(return_packet_events, ensure_ascii=False))

    def test_finalize_live_agent_meeting_records_safe_persona_artifact_contract_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            meeting_dir = output_root / "meetings" / "resident-m1"
            persona_dir = output_root / "personas" / "yanagi"
            meeting_dir.mkdir(parents=True)
            save_persona_card(
                persona_dir / "card.json",
                PersonaCard(
                    id="yanagi",
                    display_name="Yanagi",
                    lorebook=[PersonaLoreEntry(key="secret", content="RAW_LORE_SECRET_MARKER")],
                    ignored_features={"low_level_access": 1},
                ),
            )
            meeting = _resident_live_meeting()
            meeting["character_mode"] = {
                "version": 1,
                "agents": [
                    {
                        "agent_id": "agent-a",
                        "card_id": "yanagi",
                        "mode": "work_speech_only",
                        "source_path": "personas/yanagi/card.json",
                        "ignored_features": {"low_level_access": 1},
                    }
                ],
            }
            write_live_state(meeting_dir, meeting)
            request = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "resident-m1",
                    "target_agent_id": "agent-a",
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "official prompt",
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
                    "source_event_id": request["id"],
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "{{char}} leaks RAW_LORE_SECRET_MARKER and mentions low_level_access.",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )

            result = finalize_live_agent_meeting(meeting_dir)

            self.assertEqual(result["status"], "finalized")
            meeting_record = json.loads((meeting_dir / "meeting.json").read_text(encoding="utf-8"))
            report = meeting_record["persona_artifact_contract"]
            codes = {
                code
                for artifact in report["artifacts"]
                for code in artifact.get("codes", [])
            }
            serialized_report = json.dumps(report, ensure_ascii=False)
            self.assertEqual(report["status"], "violation")
            self.assertIn("unreplaced_variable", codes)
            self.assertIn("raw_card_text", codes)
            self.assertIn("ignored_feature_name", codes)
            self.assertNotIn("RAW_LORE_SECRET_MARKER", serialized_report)
            self.assertEqual(meeting_record["event_log"][-1]["kind"], "persona_artifact_contract")

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

    def test_finalize_live_agent_meeting_can_close_pending_turns_with_cancellation_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, _resident_live_meeting())
            answered_request = append_live_event(
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
                    "source_event_id": answered_request["id"],
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "Architect official answer.",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )
            pending_request = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "resident-m1",
                    "target_agent_id": "agent-b",
                    "role_id": "critic",
                    "display_name": "Critic",
                    "content": "secret pending critic prompt",
                    "turn_id": "round_1:1:critic",
                    "turn_index": 1,
                },
            )

            with self.assertRaisesRegex(ValueError, pending_request["id"]):
                finalize_live_agent_meeting(meeting_dir)

            result = finalize_live_agent_meeting(meeting_dir, close_pending=True)

            self.assertEqual(result["status"], "finalized")
            self.assertEqual(result["official_event_count"], 1)
            self.assertEqual(result["cancelled_pending_count"], 1)
            self.assertEqual(result["cancelled_turn_request_ids"], [pending_request["id"]])
            cancellation_events = [
                event for event in read_live_events(meeting_dir, limit=None) if event.get("kind") == "live_agent_turn_cancelled"
            ]
            self.assertEqual(len(cancellation_events), 1)
            cancellation = cancellation_events[0]
            self.assertEqual(cancellation["source_event_id"], pending_request["id"])
            self.assertEqual(cancellation["target_agent_id"], "agent-b")
            self.assertEqual(cancellation["role_id"], "critic")
            self.assertFalse(cancellation["official_record"])
            self.assertEqual(cancellation["channel"], "system")
            public_text = "\n".join(
                [
                    (meeting_dir / "transcript.md").read_text(encoding="utf-8"),
                    (meeting_dir / "decision.md").read_text(encoding="utf-8"),
                    (meeting_dir / "shared_memory" / "rolling-summary.md").read_text(encoding="utf-8"),
                    json.dumps(cancellation_events, ensure_ascii=False),
                ]
            )
            self.assertIn("Architect official answer.", public_text)
            self.assertNotIn("secret pending critic prompt", public_text)

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

    def test_finalize_live_agent_meeting_ignores_review_checkpoint_requests(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "resident-m1"
            meeting_dir.mkdir(parents=True)
            write_live_state(meeting_dir, _resident_live_meeting())
            official_request = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "resident-m1",
                    "target_agent_id": "agent-a",
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "private official prompt",
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
                    "source_event_id": official_request["id"],
                    "role_id": "architect",
                    "display_name": "Architect",
                    "content": "Official answer.",
                    "turn_id": "round_1:0:architect",
                    "turn_index": 0,
                },
            )
            review_request = append_live_event(
                meeting_dir,
                {
                    "kind": "live_agent_turn_request",
                    "meeting_id": "resident-m1",
                    "target_agent_id": "agent-b",
                    "role_id": "critic",
                    "display_name": "Critic",
                    "content": "private review checkpoint prompt",
                    "turn_id": "checkpoint-1",
                    "turn_index": 0,
                    "review_checkpoint_id": "checkpoint-1",
                    "channel": "review",
                    "official_record": False,
                },
            )
            append_live_event(
                meeting_dir,
                {
                    "kind": "message",
                    "meeting_id": "resident-m1",
                    "actor_id": "agent-b",
                    "target_agent_id": "agent-b",
                    "source_event_id": review_request["id"],
                    "role_id": "critic",
                    "display_name": "Critic",
                    "content": "private review checkpoint reply",
                    "turn_id": "checkpoint-1",
                    "turn_index": 0,
                    "review_checkpoint_id": "checkpoint-1",
                    "channel": "review",
                    "official_record": False,
                },
            )

            result = finalize_live_agent_meeting(meeting_dir)

            self.assertEqual(result["status"], "finalized")
            self.assertEqual(result["official_event_count"], 1)
            public_text = "\n".join(
                [
                    (meeting_dir / "transcript.md").read_text(encoding="utf-8"),
                    (meeting_dir / "decision.md").read_text(encoding="utf-8"),
                    (meeting_dir / "shared_memory" / "rolling-summary.md").read_text(encoding="utf-8"),
                    (meeting_dir / "shared_memory" / "index.json").read_text(encoding="utf-8"),
                    *(path.read_text(encoding="utf-8") for path in sorted((meeting_dir / "return_packets").glob("*.*"))),
                ]
            )
            self.assertIn("Official answer.", public_text)
            self.assertNotIn("private review checkpoint prompt", public_text)
            self.assertNotIn("private review checkpoint reply", public_text)

    def test_finalize_live_agent_meeting_requires_full_review_checkpoint_signature_before_skipping_pending(self):
        suspicious_payloads = [
            (
                "review channel only",
                {
                    "channel": "review",
                    "official_record": False,
                },
            ),
            (
                "review id without review channel",
                {
                    "review_checkpoint_id": "checkpoint-1",
                    "official_record": False,
                },
            ),
            (
                "official review checkpoint",
                {
                    "review_checkpoint_id": "checkpoint-1",
                    "channel": "review",
                    "official_record": True,
                },
            ),
        ]
        for case_name, extra_payload in suspicious_payloads:
            with self.subTest(case_name), tempfile.TemporaryDirectory() as temp_dir:
                meeting_dir = Path(temp_dir) / "meetings" / "resident-m1"
                meeting_dir.mkdir(parents=True)
                write_live_state(meeting_dir, _resident_live_meeting())
                official_request = append_live_event(
                    meeting_dir,
                    {
                        "kind": "live_agent_turn_request",
                        "meeting_id": "resident-m1",
                        "target_agent_id": "agent-a",
                        "role_id": "architect",
                        "display_name": "Architect",
                        "content": "private official prompt",
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
                        "source_event_id": official_request["id"],
                        "role_id": "architect",
                        "display_name": "Architect",
                        "content": "Official answer.",
                        "turn_id": "round_1:0:architect",
                        "turn_index": 0,
                    },
                )
                suspicious_request = append_live_event(
                    meeting_dir,
                    {
                        "kind": "live_agent_turn_request",
                        "meeting_id": "resident-m1",
                        "target_agent_id": "agent-b",
                        "role_id": "critic",
                        "display_name": "Critic",
                        "content": "suspicious pending prompt",
                        "turn_id": "checkpoint-1",
                        "turn_index": 0,
                        **extra_payload,
                    },
                )

                with self.assertRaisesRegex(ValueError, suspicious_request["id"]):
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
