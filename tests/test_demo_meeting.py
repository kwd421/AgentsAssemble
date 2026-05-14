import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agentsassemble.adapters.registry as registry_module
from agentsassemble.adapters.base import ProviderAdapter
from agentsassemble.adapters.registry import ProviderCapabilities, default_provider_registry
from agentsassemble.meeting import run_demo_meeting


class DemoMeetingTests(unittest.TestCase):
    def test_mock_demo_creates_required_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_demo_meeting(adapter_name="mock", output_root=Path(temp_dir))

            meeting_dir = result.meeting_dir
            self.assertTrue(meeting_dir.exists())

            self.assertTrue((meeting_dir / "agenda.md").exists())
            self.assertTrue((meeting_dir / "transcript.md").exists())
            self.assertTrue((meeting_dir / "decision.md").exists())
            self.assertTrue((meeting_dir / "meeting.json").exists())

            meeting = json.loads((meeting_dir / "meeting.json").read_text(encoding="utf-8"))
            live_events = [
                json.loads(line)
                for line in (meeting_dir / "live_events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(meeting["adapter_config"]["name"], "mock")
            self.assertEqual(meeting["live_status"], "complete")
            self.assertTrue(any(event["kind"] == "message" for event in live_events))
            self.assertFalse(any(event["kind"] == "reaction" for event in live_events))
            self.assertTrue(any(event["kind"] == "synthesis" for event in live_events))
            self.assertEqual(meeting["provider_configs"]["mock"]["kind"], "mock")
            self.assertEqual(meeting["provider_configs"]["mock"]["display_name"], "Mock Demo")
            self.assertEqual(meeting["permission_profiles"]["meeting_read_only"]["implementation"], False)
            self.assertEqual(meeting["permission_profiles"]["meeting_read_only"]["filesystem_write"], False)
            self.assertEqual(meeting["provider_capabilities"]["mock"]["supports_structured_output"], True)
            self.assertEqual(meeting["provider_capabilities"]["mock"]["supports_web_search"], False)
            self.assertEqual(
                [binding["provider_id"] for binding in meeting["agent_bindings"]],
                ["mock", "mock", "mock"],
            )
            self.assertEqual(meeting["research_depth"]["name"], "smoke")
            self.assertEqual(meeting["research_steering"]["stance"], "open")
            self.assertEqual(meeting["meeting_template"]["id"], "one_piece_admiral_debate_v0")
            self.assertEqual(meeting["moderator_control"]["default_official_engagement"], "moderator_called")
            self.assertEqual(meeting["moderator_control"]["informal_default_engagement"], "mentioned")
            self.assertIn("official", meeting["moderator_control"]["official_record_channels"])
            self.assertIn("commit", meeting["moderator_control"]["host_approval_required_for"])
            self.assertEqual(
                [round_definition["id"] for round_definition in meeting["meeting_template"]["rounds"]],
                ["round_1", "round_2"],
            )
            self.assertEqual(
                [round_definition["turn_control"]["selection"] for round_definition in meeting["meeting_template"]["rounds"]],
                ["all_roles", "all_roles"],
            )
            self.assertTrue(all("engagement_mode" in binding for binding in meeting["agent_bindings"]))
            self.assertEqual(meeting["evidence_gate"]["status"], "pass")
            self.assertEqual(meeting["decision_gate"]["status"], "split_decision")
            self.assertEqual(meeting["decision_gate"]["required_action"], "record_split_decision")
            self.assertEqual(meeting["decision_status"]["status"], "partial")
            self.assertIn("next_actions", meeting["decision_status"])
            self.assertIn("Record split decision", meeting["decision_status"]["next_actions"][0])
            self.assertNotIn("Run another round or request a user decision.", meeting["decision_status"]["next_actions"])
            self.assertEqual(
                [event["kind"] for event in meeting["event_log"]],
                [
                    "meeting_started",
                    "role_sessions_started",
                    "research_completed",
                    "debate_completed",
                    "synthesis_completed",
                    "artifacts_written",
                ],
            )
            self.assertEqual(meeting["event_log"][2]["payload"]["evidence_gate_status"], "pass")
            self.assertEqual(meeting["question"], "Who is the strongest One Piece admiral?")
            self.assertEqual(
                [role["id"] for role in meeting["roles"]],
                ["lore_lawyer", "show_me_the_feats", "fanboard_skeptic"],
            )
            self.assertEqual(
                [role["display_name"] for role in meeting["roles"]],
                ["설정충", "공식이뭘알아", "만갤러"],
            )
            for round_record in meeting["debate_rounds"]:
                self.assertEqual(round_record["turn_control"]["selection"], "all_roles")
                self.assertEqual(round_record["turn_control"]["non_speaker_mode"], "watch")
                self.assertIn("skipped_role_ids", round_record["turn_control"])
                for index, message in enumerate(round_record["messages"]):
                    self.assertEqual(message["turn_index"], index)
                    self.assertEqual(message["engagement_mode"], "moderator_called")
                    self.assertTrue(message["turn_id"].startswith(f"{round_record['id']}:"))

            transcript = (meeting_dir / "transcript.md").read_text(encoding="utf-8")
            self.assertIn("## Round 1", transcript)
            self.assertIn("## Round 2", transcript)
            self.assertIn("Position:", transcript)
            self.assertIn("Change conditions:", transcript)
            self.assertIn("## Moderator Synthesis", transcript)
            self.assertIn("Informal lobby and side chat are excluded from this official transcript", transcript)
            agenda = (meeting_dir / "agenda.md").read_text(encoding="utf-8")
            self.assertIn("Meeting template: 원피스 3대장 최강자 토론", agenda)
            decision = (meeting_dir / "decision.md").read_text(encoding="utf-8")
            self.assertIn("## Decision Gate", decision)
            self.assertIn("Status: split_decision", decision)

            for role_id in ("lore_lawyer", "show_me_the_feats", "fanboard_skeptic"):
                self.assertTrue((meeting_dir / "private_research" / role_id / "research.md").exists())
                self.assertTrue((meeting_dir / "private_research" / role_id / "research.json").exists())
                self.assertTrue((meeting_dir / "roles" / role_id / "memory.md").exists())
                self.assertTrue((meeting_dir / "roles" / role_id / "history.jsonl").exists())
                self.assertTrue((meeting_dir / "tasks" / f"{role_id}.md").exists())
                self.assertTrue((meeting_dir / "return_packets" / f"{role_id}.md").exists())
                self.assertTrue((meeting_dir / "return_packets" / f"{role_id}.json").exists())
                self.assertTrue((Path(temp_dir) / "memory" / "agents" / f"{role_id}.md").exists())
                isolation = meeting["isolation"][role_id]
                self.assertEqual(isolation["agent_binding"]["role_id"], role_id)
                self.assertEqual(isolation["provider"]["kind"], "mock")
                self.assertEqual(isolation["permissions"]["implementation"], False)
            packet = json.loads((meeting_dir / "return_packets" / "fanboard_skeptic.json").read_text(encoding="utf-8"))
            packet_md = (meeting_dir / "return_packets" / "fanboard_skeptic.md").read_text(encoding="utf-8")
            self.assertEqual(packet["role_id"], "fanboard_skeptic")
            self.assertIn(packet["decision"]["outcome_for_role"], {"won_or_partially_supported", "lost_or_not_selected", "unresolved"})
            self.assertEqual(packet["decision_status"]["status"], meeting["decision_status"]["status"])
            self.assertIn("follow_up", packet)
            self.assertIn("handoff_checklist", packet)
            self.assertIn("Review decision status before continuing work.", packet["handoff_checklist"])
            self.assertIn("## Handoff Checklist", packet_md)
            self.assertIn("## Decision Status", packet_md)
            self.assertTrue(packet["stance"]["history"])
            self.assertIn("what_happened", packet["answer_prompts"])
            self.assertEqual(meeting["artifacts"]["return_packets"], "return_packets/")
            self.assertIn("fanboard_skeptic", meeting["return_packets"])
            self.assertTrue((Path(temp_dir) / "memory" / "project.md").exists())
            self.assertTrue((Path(temp_dir) / "memory" / "episodes.jsonl").exists())
            self.assertTrue((Path(temp_dir) / "memory" / "reflections" / f"{result.meeting_id}.md").exists())
            self.assertEqual(meeting["memory_artifacts"]["project"], "memory/project.md")

    def test_research_depth_changes_mock_source_volume(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            smoke = run_demo_meeting(adapter_name="mock", output_root=root, research_depth="smoke")
            deep = run_demo_meeting(adapter_name="mock", output_root=root, research_depth="deep")

            smoke_research = json.loads(
                (smoke.meeting_dir / "private_research" / "lore_lawyer" / "research.json").read_text(
                    encoding="utf-8"
                )
            )
            deep_research = json.loads(
                (deep.meeting_dir / "private_research" / "lore_lawyer" / "research.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(smoke_research["research_depth"]["name"], "smoke")
            self.assertEqual(deep_research["research_depth"]["name"], "deep")
            self.assertLess(len(smoke_research["sources"]), len(deep_research["sources"]))
            self.assertEqual(len(smoke_research["sources"]), 8)
            self.assertEqual(len(deep_research["sources"]), 45)
            self.assertEqual(len(smoke_research["claim_evidence"]), 3)
            self.assertEqual(len(deep_research["claim_evidence"]), 12)
            self.assertEqual(len(smoke_research["counterclaims"]), 1)
            self.assertEqual(len(deep_research["counterclaims"]), 6)

    def test_free_chat_writes_room_log_without_official_decision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_demo_meeting(adapter_name="mock", output_root=Path(temp_dir), meeting_mode="free_chat")
            meeting_dir = result.meeting_dir
            meeting = json.loads((meeting_dir / "meeting.json").read_text(encoding="utf-8"))
            live_events = [
                json.loads(line)
                for line in (meeting_dir / "live_events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            room_log_exists = (meeting_dir / "room-log.md").exists()
            decision_exists = (meeting_dir / "decision.md").exists()
            transcript_exists = (meeting_dir / "transcript.md").exists()
            private_research_exists = (meeting_dir / "private_research").exists()
            agenda = (meeting_dir / "agenda.md").read_text(encoding="utf-8")

        self.assertEqual(meeting["meeting_mode"], "free_chat")
        self.assertEqual(meeting["moderator"]["enabled"], True)
        self.assertEqual(meeting["debate_rounds"], [])
        self.assertEqual(meeting["decision_gate"]["status"], "no_official_decision")
        self.assertFalse(meeting["decision_gate"]["can_finalize"])
        self.assertEqual(meeting["decision_status"]["status"], "not_applicable")
        self.assertEqual(meeting["failure_state"]["status"], "not_applicable")
        self.assertEqual(meeting["failure_state"]["decision_gate_status"], "no_official_decision")
        self.assertIn("room_log", meeting["artifacts"])
        self.assertTrue(room_log_exists)
        self.assertFalse(decision_exists)
        self.assertFalse(transcript_exists)
        self.assertFalse(private_research_exists)
        self.assertIn("Meeting mode: free_chat", agenda)
        self.assertIn("Informal room chat", agenda)
        self.assertNotIn("Independent research", agenda)
        self.assertNotIn("Moderator synthesis", agenda)
        self.assertTrue(any(event["kind"] == "room_chat" for event in live_events))
        self.assertFalse(any(event["kind"] == "message" for event in live_events))
        self.assertFalse(any(event["kind"] == "synthesis" for event in live_events))
        room_chat_events = [event for event in live_events if event["kind"] == "room_chat"]
        self.assertTrue(room_chat_events)
        self.assertTrue(all(event["channel"] == "side_chat" for event in room_chat_events))
        self.assertTrue(all(event["official_record"] is False for event in room_chat_events))
        self.assertIn("free_chat_recorded", [event["kind"] for event in meeting["event_log"]])

    def test_free_chat_failure_is_recorded_in_failure_state(self):
        class FailingFreeChatAdapter(ProviderAdapter):
            name = "failing_free_chat"

            def start_session(self, role, meeting_context):
                return {"role_id": role.id, "session_id": role.id}

            def run_research(self, role, session, question, depth, steering):
                raise AssertionError("free_chat should not run research")

            def run_round(self, role, session, round_name, prompt, public_context):
                raise RuntimeError("room unavailable")

            def synthesize(self, session, question, public_context):
                raise AssertionError("free_chat should not synthesize")

        def registry_with_failing_free_chat(*args, **kwargs):
            registry = default_provider_registry(*args, **kwargs)
            registry.register(
                "failing_free_chat",
                lambda _provider: FailingFreeChatAdapter(),
                ProviderCapabilities(
                    supports_research=True,
                    supports_web_search=False,
                    supports_tools=False,
                    supports_filesystem=False,
                    supports_session_resume=False,
                    supports_structured_output=False,
                ),
            )
            return registry

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            agent_config = root / "agents.json"
            agent_config.write_text(
                """
{
  "providers": [{"id": "failing", "kind": "failing_free_chat", "display_name": "Failing Free Chat"}],
  "permission_profiles": [{"id": "read_only"}],
  "agent_bindings": [
    {"agent_id": "a", "role_id": "lore_lawyer", "provider_id": "failing", "permission_profile_id": "read_only"},
    {"agent_id": "b", "role_id": "show_me_the_feats", "provider_id": "failing", "permission_profile_id": "read_only"},
    {"agent_id": "c", "role_id": "fanboard_skeptic", "provider_id": "failing", "permission_profile_id": "read_only"}
  ]
}
""",
                encoding="utf-8",
            )
            with patch("agentsassemble.meeting_setup.default_provider_registry", registry_with_failing_free_chat):
                result = run_demo_meeting(
                    adapter_name="mock",
                    output_root=root,
                    agent_config_path=agent_config,
                    meeting_mode="free_chat",
                )
            meeting = json.loads((result.meeting_dir / "meeting.json").read_text(encoding="utf-8"))

        self.assertEqual(meeting["decision_gate"]["status"], "no_official_decision")
        self.assertEqual(meeting["failure_state"]["status"], "degraded")
        self.assertIn("room_chat_failed:lore_lawyer", meeting["failure_state"]["failures"])
        self.assertTrue(all(message["status"] == "failed" for message in meeting["room_chat"]))

    def test_moderator_off_skips_synthesis_and_requires_user_decision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_demo_meeting(adapter_name="mock", output_root=Path(temp_dir), moderator_enabled=False)
            meeting_dir = result.meeting_dir
            meeting = json.loads((meeting_dir / "meeting.json").read_text(encoding="utf-8"))
            live_events = [
                json.loads(line)
                for line in (meeting_dir / "live_events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            decision = (meeting_dir / "decision.md").read_text(encoding="utf-8")

        self.assertEqual(meeting["meeting_mode"], "debate")
        self.assertEqual(meeting["moderator"]["enabled"], False)
        self.assertEqual(meeting["moderator_synthesis"]["status"], "moderator_disabled")
        self.assertEqual(meeting["decision_gate"]["status"], "needs_user_decision")
        self.assertFalse(meeting["decision_gate"]["can_finalize"])
        self.assertEqual(meeting["decision_gate"]["required_action"], "user_decision")
        self.assertEqual(meeting["decision_status"]["status"], "pending_user")
        self.assertEqual(meeting["failure_state"]["status"], "action_required")
        self.assertEqual(meeting["failure_state"]["decision_gate_status"], "needs_user_decision")
        self.assertEqual(meeting["failure_state"]["required_action"], "user_decision")
        self.assertTrue(meeting["debate_rounds"])
        self.assertFalse(any(event["kind"] == "synthesis" for event in live_events))
        self.assertIn("synthesis_skipped", [event["kind"] for event in meeting["event_log"]])
        self.assertIn("Status: needs_user_decision", decision)
        self.assertIn("Moderator is disabled", decision)

    def test_research_steering_is_recorded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_demo_meeting(
                adapter_name="mock",
                output_root=Path(temp_dir),
                research_steering="키자루가 최강이라는 관점을 더 자세히 조사",
            )
            meeting = json.loads((result.meeting_dir / "meeting.json").read_text(encoding="utf-8"))
            research = json.loads(
                (result.meeting_dir / "private_research" / "fanboard_skeptic" / "research.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(meeting["research_steering"]["stance"], "user_leaning")
            self.assertIn("키자루", meeting["research_steering"]["prompt"])
            self.assertEqual(research["research_steering"]["stance"], "user_leaning")

    def test_agent_config_records_host_approved_bindings_and_incoming_agents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            agent_config = root / "agents.json"
            agent_config.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "approved-mock",
                                "kind": "mock",
                                "display_name": "Approved Mock Provider",
                            }
                        ],
                        "permission_profiles": [
                            {
                                "id": "meeting_guest_readonly",
                                "meeting_read": True,
                                "official_turn": True,
                                "filesystem_write": False,
                                "implementation": False,
                            }
                        ],
                        "incoming_agents": [
                            {
                                "name": "친구봇",
                                "requested_role": "lore_lawyer",
                                "provider": "cursor",
                                "auth_ref": "literal:incoming-secret",
                                "endpoint": "https://friend.example/run?api_key=incoming-secret",
                                "notes": "Bearer incoming-secret",
                                "requested_permissions": {"filesystem_write": True},
                            }
                        ],
                        "agent_bindings": [
                            {
                                "agent_id": "approved-lore",
                                "role_id": "lore_lawyer",
                                "owner_id": "host",
                                "provider_id": "approved-mock",
                                "permission_profile_id": "meeting_guest_readonly",
                            },
                            {
                                "agent_id": "approved-feats",
                                "role_id": "show_me_the_feats",
                                "owner_id": "host",
                                "provider_id": "approved-mock",
                                "permission_profile_id": "meeting_guest_readonly",
                            },
                            {
                                "agent_id": "approved-skeptic",
                                "role_id": "fanboard_skeptic",
                                "owner_id": "host",
                                "provider_id": "approved-mock",
                                "permission_profile_id": "meeting_guest_readonly",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = run_demo_meeting(adapter_name="mock", output_root=root, agent_config_path=agent_config)
            meeting_text = (result.meeting_dir / "meeting.json").read_text(encoding="utf-8")
            meeting = json.loads(meeting_text)

            self.assertEqual(meeting["agent_config_source"], str(agent_config))
            self.assertEqual(meeting["provider_configs"]["approved-mock"]["display_name"], "Approved Mock Provider")
            self.assertEqual(
                [binding["agent_id"] for binding in meeting["agent_bindings"]],
                ["approved-lore", "approved-feats", "approved-skeptic"],
            )
            self.assertEqual(meeting["incoming_agents"][0]["name"], "친구봇")
            self.assertNotIn("incoming-secret", meeting_text)
            self.assertEqual(meeting["incoming_agents"][0]["auth_ref"], "<redacted>")
            self.assertEqual(meeting["incoming_agents"][0]["endpoint"], "<redacted>")
            self.assertEqual(meeting["incoming_agents"][0]["notes"], "<redacted>")
            self.assertEqual(meeting["isolation"]["lore_lawyer"]["agent_binding"]["owner_id"], "host")

    def test_incoming_agents_require_host_admission_before_execution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            agent_config = root / "agents.json"
            agent_config.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "approved-mock",
                                "kind": "mock",
                                "display_name": "Approved Mock Provider",
                            }
                        ],
                        "permission_profiles": [
                            {
                                "id": "meeting_guest_readonly",
                                "meeting_read": True,
                                "official_turn": True,
                                "filesystem_write": False,
                                "implementation": False,
                            }
                        ],
                        "incoming_agents": [
                            {
                                "name": "친구봇",
                                "requested_role": "show_me_the_feats",
                                "provider": "cursor",
                                "approved_binding_agent_id": "approved-feats",
                                "requested_permissions": {
                                    "meeting_read": True,
                                    "official_turn": True,
                                    "filesystem_write": False,
                                    "implementation": False,
                                },
                            },
                            {
                                "name": "위험한봇",
                                "requested_role": "unknown_role",
                                "provider": "grok",
                                "requested_permissions": {
                                    "meeting_read": True,
                                    "official_turn": True,
                                    "filesystem_write": True,
                                    "implementation": True,
                                },
                            },
                        ],
                        "agent_bindings": [
                            {
                                "agent_id": "approved-lore",
                                "role_id": "lore_lawyer",
                                "owner_id": "host",
                                "provider_id": "approved-mock",
                                "permission_profile_id": "meeting_guest_readonly",
                            },
                            {
                                "agent_id": "approved-feats",
                                "role_id": "show_me_the_feats",
                                "owner_id": "friend",
                                "provider_id": "approved-mock",
                                "permission_profile_id": "meeting_guest_readonly",
                                "join_mode": "current_session",
                            },
                            {
                                "agent_id": "approved-skeptic",
                                "role_id": "fanboard_skeptic",
                                "owner_id": "host",
                                "provider_id": "approved-mock",
                                "permission_profile_id": "meeting_guest_readonly",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = run_demo_meeting(adapter_name="mock", output_root=root, agent_config_path=agent_config)
            meeting = json.loads((result.meeting_dir / "meeting.json").read_text(encoding="utf-8"))

            decisions = {decision["name"]: decision for decision in meeting["admission_decisions"]}
            self.assertEqual(decisions["친구봇"]["status"], "approved")
            self.assertEqual(decisions["친구봇"]["execution"], "bound_to_meeting_role")
            self.assertEqual(decisions["친구봇"]["effective_role_id"], "show_me_the_feats")
            self.assertEqual(decisions["친구봇"]["effective_provider_id"], "approved-mock")
            self.assertEqual(decisions["친구봇"]["permission_profile_id"], "meeting_guest_readonly")
            self.assertEqual(decisions["위험한봇"]["status"], "rejected")
            self.assertEqual(decisions["위험한봇"]["execution"], "not_executed")
            self.assertIn("unknown_requested_role", decisions["위험한봇"]["reasons"])
            self.assertIn("requested_permissions_exceed_meeting_mode", decisions["위험한봇"]["reasons"])

    def test_remote_bridge_agent_participates_in_real_meeting_path(self):
        bridge_calls = []

        def fake_bridge(url, headers, payload, timeout_seconds):
            bridge_calls.append({"url": url, "headers": headers, "payload": payload, "timeout_seconds": timeout_seconds})
            step = payload["step"]
            if step == "research":
                return {
                    "text": json.dumps(
                        {
                            "queries": ["friend claude query"],
                            "sources": [
                                {
                                    "url": "https://example.com/friend-claude",
                                    "title": "Friend Claude source",
                                    "source_type": "analysis",
                                    "quality": "medium",
                                    "note": "친구 Claude Code가 낸 근거",
                                    "snippet": "요약",
                                    "extracted_notes": ["note"],
                                }
                            ],
                            "summary": "친구 Claude Code 리서치",
                            "confidence": "medium",
                            "uncertainty": "",
                            "coverage_gaps": [],
                            "claim_evidence": [
                                {
                                    "claim": "친구 Claude Code 주장",
                                    "evidence": ["https://example.com/friend-claude"],
                                    "evidence_relation": "supports",
                                    "interpretation": "bridge test",
                                    "confidence": "medium",
                                    "source_quality": "medium",
                                }
                            ],
                            "counterclaims": [],
                            "rejected_claims": [],
                        },
                        ensure_ascii=False,
                    ),
                    "metadata": {"bridge": "friend-mac", "step": step},
                }
            return {
                "text": json.dumps(
                    {
                        "content": "친구 Claude Code가 실제 회의 라운드에 참가함",
                        "position": "아카이누 우세",
                        "stance_status": "held",
                        "change_conditions": ["더 강한 반례"],
                        "confidence": "medium",
                    },
                    ensure_ascii=False,
                ),
                "metadata": {"bridge": "friend-mac", "step": step},
            }

        original_requester = registry_module.REMOTE_BRIDGE_REQUESTER
        registry_module.REMOTE_BRIDGE_REQUESTER = fake_bridge
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                agent_config = root / "agents.json"
                agent_config.write_text(
                    json.dumps(
                        {
                            "providers": [
                                {
                                    "id": "host-mock",
                                    "kind": "mock",
                                    "display_name": "Host Mock",
                                },
                                {
                                    "id": "friend-claude-code",
                                    "kind": "remote_http_bridge",
                                    "display_name": "Friend Claude Code",
                                    "endpoint": "http://100.64.0.10:8777",
                                    "auth_ref": "literal:bridge-token",
                                    "timeout_seconds": 120,
                                },
                            ],
                            "permission_profiles": [
                                {
                                    "id": "meeting_readonly",
                                    "meeting_read": True,
                                    "official_turn": True,
                                    "filesystem_write": False,
                                    "implementation": False,
                                }
                            ],
                            "agent_bindings": [
                                {
                                    "agent_id": "host-lore",
                                    "role_id": "lore_lawyer",
                                    "owner_id": "host",
                                    "provider_id": "host-mock",
                                    "permission_profile_id": "meeting_readonly",
                                },
                                {
                                    "agent_id": "friend-claude",
                                    "role_id": "show_me_the_feats",
                                    "owner_id": "friend",
                                    "provider_id": "friend-claude-code",
                                    "permission_profile_id": "meeting_readonly",
                                    "join_mode": "current_session",
                                },
                                {
                                    "agent_id": "host-skeptic",
                                    "role_id": "fanboard_skeptic",
                                    "owner_id": "host",
                                    "provider_id": "host-mock",
                                    "permission_profile_id": "meeting_readonly",
                                },
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

                result = run_demo_meeting(adapter_name="mock", output_root=root, agent_config_path=agent_config)
                meeting = json.loads((result.meeting_dir / "meeting.json").read_text(encoding="utf-8"))

            self.assertTrue(bridge_calls)
            self.assertEqual(bridge_calls[0]["headers"]["Authorization"], "Bearer bridge-token")
            self.assertEqual(bridge_calls[0]["payload"]["owner_id"], "friend")
            self.assertEqual(bridge_calls[0]["payload"]["join_mode"], "current_session")
            self.assertEqual(
                meeting["isolation"]["show_me_the_feats"]["provider"]["kind"],
                "remote_http_bridge",
            )
            remote_messages = [
                message
                for round_record in meeting["debate_rounds"]
                for message in round_record["messages"]
                if message["role_id"] == "show_me_the_feats"
            ]
            self.assertTrue(any(message.get("bridge", {}).get("bridge") == "friend-mac" for message in remote_messages))
            self.assertTrue(any("친구 Claude Code" in message["content"] for message in remote_messages))
        finally:
            registry_module.REMOTE_BRIDGE_REQUESTER = original_requester

    def test_round_one_does_not_include_other_private_research(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_demo_meeting(adapter_name="mock", output_root=Path(temp_dir))
            meeting = json.loads((result.meeting_dir / "meeting.json").read_text(encoding="utf-8"))

            round_one = meeting["debate_rounds"][0]["messages"]
            for message in round_one:
                own_role = message["role_id"]
                self.assertEqual(message["stance_status"], "held")
                self.assertTrue(message["position"])
                self.assertTrue(message["change_conditions"])
                for other_role in ("lore_lawyer", "show_me_the_feats", "fanboard_skeptic"):
                    if other_role != own_role:
                        self.assertNotIn(f"private_research/{other_role}", message["content"])

    def test_custom_council_config_controls_debate_rounds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "council.json"
            config_path.write_text(
                json.dumps(
                    {
                        "topic": "topic",
                        "question": "question",
                        "roles": [
                            {
                                "id": "lore_lawyer",
                                "display_name": "설정충",
                                "lens": "Canon Analyst",
                                "research_focus": "canon",
                            },
                            {
                                "id": "show_me_the_feats",
                                "display_name": "공식이뭘알아",
                                "lens": "Feats Analyst",
                                "research_focus": "feats",
                            },
                            {
                                "id": "fanboard_skeptic",
                                "display_name": "만갤러",
                                "lens": "Skeptical Critic",
                                "research_focus": "skeptic",
                            },
                        ],
                        "meeting_template": {
                            "id": "custom_three_rounds",
                            "display_name": "Custom Three Rounds",
                            "rounds": [
                                {
                                    "id": "round_1",
                                    "title": "Opening",
                                    "report_label": "Opening",
                                    "context_scope": "own_research",
                                    "instruction": "Open.",
                                },
                                {
                                    "id": "round_2",
                                    "title": "Rebuttal",
                                    "report_label": "Rebuttal",
                                    "context_scope": "public_debate",
                                    "instruction": "Rebut.",
                                },
                                {
                                    "id": "round_3",
                                    "title": "Final",
                                    "report_label": "Final",
                                    "context_scope": "public_debate",
                                    "instruction": "Final.",
                                },
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = run_demo_meeting(adapter_name="mock", output_root=root, council_config_path=config_path)
            meeting = json.loads((result.meeting_dir / "meeting.json").read_text(encoding="utf-8"))

        self.assertEqual(meeting["meeting_template"]["id"], "custom_three_rounds")
        self.assertEqual([round_record["id"] for round_record in meeting["debate_rounds"]], ["round_1", "round_2", "round_3"])

    def test_mock_demo_supports_custom_role_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = run_demo_meeting(
                adapter_name="mock",
                output_root=root,
                council_config_path=Path("configs/gorilla-vs-bodybuilders.json"),
            )
            meeting = json.loads((result.meeting_dir / "meeting.json").read_text(encoding="utf-8"))

        self.assertEqual(
            [role["id"] for role in meeting["roles"]],
            ["animal_spec_nerd", "gym_tactics_bro", "playground_skeptic"],
        )
        self.assertEqual([round_record["id"] for round_record in meeting["debate_rounds"]], ["round_1", "round_2", "round_3"])

    def test_second_meeting_loads_previous_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = run_demo_meeting(adapter_name="mock", output_root=root)
            second = run_demo_meeting(adapter_name="mock", output_root=root)

            meeting = json.loads((second.meeting_dir / "meeting.json").read_text(encoding="utf-8"))
            self.assertEqual(len(meeting["memory_context"]["recent_episodes"]), 1)
            self.assertEqual(meeting["memory_context"]["recent_episodes"][0]["meeting_id"], first.meeting_id)
            role_memory = (second.meeting_dir / "roles" / "lore_lawyer" / "memory.md").read_text(
                encoding="utf-8"
            )
            self.assertIn(first.meeting_id, role_memory)

    def test_follow_up_metadata_is_recorded_in_meeting_and_agenda(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = run_demo_meeting(adapter_name="mock", output_root=root)
            second = run_demo_meeting(
                adapter_name="mock",
                output_root=root,
                follow_up_of=first.meeting_id,
                follow_up_note="Reopen unresolved caveats.",
            )

            meeting = json.loads((second.meeting_dir / "meeting.json").read_text(encoding="utf-8"))
            agenda = (second.meeting_dir / "agenda.md").read_text(encoding="utf-8")
            live_state = json.loads((second.meeting_dir / "live_state.json").read_text(encoding="utf-8"))

        self.assertEqual(meeting["follow_up"]["parent_meeting_id"], first.meeting_id)
        self.assertEqual(meeting["follow_up"]["note"], "Reopen unresolved caveats.")
        self.assertEqual(live_state["follow_up"]["parent_meeting_id"], first.meeting_id)
        self.assertIn(f"Follow-up of: {first.meeting_id}", agenda)
        self.assertIn("Follow-up note: Reopen unresolved caveats.", agenda)

    def test_follow_up_can_be_generated_from_existing_meeting_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = run_demo_meeting(adapter_name="mock", output_root=root)
            second = run_demo_meeting(
                adapter_name="mock",
                output_root=root,
                follow_up_from=first.meeting_dir,
                follow_up_note="Continue after implementation issue.",
            )

            meeting = json.loads((second.meeting_dir / "meeting.json").read_text(encoding="utf-8"))
            agenda = (second.meeting_dir / "agenda.md").read_text(encoding="utf-8")

        self.assertEqual(meeting["follow_up"]["parent_meeting_id"], first.meeting_id)
        self.assertEqual(meeting["follow_up"]["parent_meeting_dir"], str(first.meeting_dir))
        self.assertEqual(meeting["follow_up"]["artifact_refs"]["decision"], str(first.meeting_dir / "decision.md"))
        self.assertEqual(meeting["follow_up"]["artifact_refs"]["transcript"], str(first.meeting_dir / "transcript.md"))
        self.assertIn(f"Follow-up of: {first.meeting_id}", agenda)

    def test_follow_up_from_records_missing_artifact_refs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            parent_dir = root / "missing-parent"
            parent_dir.mkdir()

            result = run_demo_meeting(
                adapter_name="mock",
                output_root=root,
                follow_up_from=parent_dir,
                follow_up_note="Investigate missing parent artifacts.",
            )
            meeting = json.loads((result.meeting_dir / "meeting.json").read_text(encoding="utf-8"))

        self.assertEqual(meeting["follow_up"]["parent_meeting_dir"], str(parent_dir))
        self.assertEqual(
            set(meeting["follow_up"]["missing_refs"]),
            {"agenda", "transcript", "decision", "meeting"},
        )

    def test_agenda_is_written_before_research_can_fail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch("agentsassemble.meeting.run_research_phase", side_effect=RuntimeError("research stopped")):
                with self.assertRaisesRegex(RuntimeError, "research stopped"):
                    run_demo_meeting(adapter_name="mock", output_root=root)

            meeting_dirs = list((root / "meetings").iterdir())
            self.assertEqual(len(meeting_dirs), 1)
            agenda = (meeting_dirs[0] / "agenda.md").read_text(encoding="utf-8")
            self.assertIn("# Agenda", agenda)
            self.assertIn("원피스 3대장 중 누가 제일 센가?", agenda)
            self.assertIn("Meeting template: 원피스 3대장 최강자 토론", agenda)


if __name__ == "__main__":
    unittest.main()
