import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agentsassemble.adapters.base import ProviderAdapter
from agentsassemble.adapters.registry import ProviderCapabilities, default_provider_registry
from agentsassemble.meeting import run_demo_meeting
from agentsassemble.meeting_phases import run_research_phase
from agentsassemble.models import CouncilConfig, ResearchDepth, ResearchSteering, Role


class MaybeFailingResearchAdapter:
    def __init__(self, should_fail=False, fail_times=0):
        self.should_fail = should_fail
        self.fail_times = fail_times
        self.calls = 0

    def run_research(self, role, session, question, depth, steering):
        self.calls += 1
        if self.should_fail or self.calls <= self.fail_times:
            raise RuntimeError("provider unavailable")
        return {
            "role_id": role.id,
            "display_name": role.display_name,
            "queries": ["q"],
            "sources": [],
            "summary": f"{role.display_name} completed research.",
            "confidence": "medium",
            "uncertainty": "",
            "claim_evidence": [],
            "counterclaims": [],
        }


class PartialFailureTests(unittest.TestCase):
    def test_research_phase_records_failed_role_and_continues(self):
        roles = [
            Role("role_a", "A", "Lens", "focus"),
            Role("role_b", "B", "Lens", "focus"),
            Role("role_c", "C", "Lens", "focus"),
        ]
        config = CouncilConfig("topic", "topic", "question", "question", roles)
        depth = ResearchDepth("smoke", "Smoke", 0, 0, 0, 0, 0, 0, "", "")
        resolved_agents = {
            "role_a": SimpleNamespace(adapter=MaybeFailingResearchAdapter()),
            "role_b": SimpleNamespace(adapter=MaybeFailingResearchAdapter(should_fail=True)),
            "role_c": SimpleNamespace(adapter=MaybeFailingResearchAdapter()),
        }
        sessions = {role.id: {"session_id": role.id} for role in roles}

        with tempfile.TemporaryDirectory() as temp_dir:
            research_records, evidence_gate = run_research_phase(
                config,
                Path(temp_dir),
                sessions,
                resolved_agents,
                depth,
                ResearchSteering(),
                lambda _message: None,
            )

        self.assertEqual([record["role_id"] for record in research_records], ["role_a", "role_b", "role_c"])
        failed = research_records[1]
        self.assertEqual(failed["status"], "failed")
        self.assertIn("provider unavailable", failed["summary"])
        self.assertEqual(evidence_gate["status"], "warn")
        self.assertIn("role_b:research_failed", evidence_gate["failures"])
        self.assertEqual(failed["retry"]["attempts"], 2)

    def test_research_phase_retries_transient_failure_before_recording_success(self):
        role = Role("role_a", "A", "Lens", "focus")
        adapter = MaybeFailingResearchAdapter(fail_times=1)
        config = CouncilConfig("topic", "topic", "question", "question", [role])
        depth = ResearchDepth("smoke", "Smoke", 0, 0, 0, 0, 0, 0, "", "")
        resolved_agents = {"role_a": SimpleNamespace(adapter=adapter)}
        sessions = {"role_a": {"session_id": "role_a"}}
        live_events = []

        with tempfile.TemporaryDirectory() as temp_dir:
            research_records, evidence_gate = run_research_phase(
                config,
                Path(temp_dir),
                sessions,
                resolved_agents,
                depth,
                ResearchSteering(),
                lambda _message: None,
                live_events.append,
            )

        self.assertEqual(adapter.calls, 2)
        self.assertNotEqual(research_records[0].get("status"), "failed")
        self.assertEqual(research_records[0]["retry"]["attempts"], 2)
        self.assertEqual(research_records[0]["retry"]["status"], "recovered")
        self.assertEqual(evidence_gate["status"], "pass")
        research_event = [event for event in live_events if event.get("kind") == "research"][0]
        self.assertEqual(research_event["retry_status"], "recovered")
        self.assertEqual(research_event["retry_attempts"], 2)

    def test_research_phase_overwrites_provider_retry_metadata(self):
        class BadRetryMetadataAdapter(MaybeFailingResearchAdapter):
            def run_research(self, role, session, question, depth, steering):
                research = super().run_research(role, session, question, depth, steering)
                research["retry"] = "provider-owned string should not control orchestrator metadata"
                return research

        role = Role("role_a", "A", "Lens", "focus")
        config = CouncilConfig("topic", "topic", "question", "question", [role])
        depth = ResearchDepth("smoke", "Smoke", 0, 0, 0, 0, 0, 0, "", "")
        resolved_agents = {"role_a": SimpleNamespace(adapter=BadRetryMetadataAdapter())}
        sessions = {"role_a": {"session_id": "role_a"}}
        live_events = []

        with tempfile.TemporaryDirectory() as temp_dir:
            research_records, _evidence_gate = run_research_phase(
                config,
                Path(temp_dir),
                sessions,
                resolved_agents,
                depth,
                ResearchSteering(),
                lambda _message: None,
                live_events.append,
            )

        self.assertIsInstance(research_records[0]["retry"], dict)
        self.assertEqual(research_records[0]["retry"]["status"], "not_needed")
        research_event = [event for event in live_events if event.get("kind") == "research"][0]
        self.assertEqual(research_event["retry_status"], "not_needed")

    def test_full_meeting_continues_when_one_research_adapter_fails(self):
        class OneRoleFailingAdapter(ProviderAdapter):
            name = "one_role_failing"

            def start_session(self, role, meeting_context):
                return {"role_id": role.id, "session_id": role.id}

            def run_research(self, role, session, question, depth, steering):
                if role.id == "show_me_the_feats":
                    raise RuntimeError("provider unavailable")
                return MaybeFailingResearchAdapter().run_research(role, session, question, depth, steering)

            def run_round(self, role, session, round_name, prompt, public_context):
                return {
                    "role_id": role.id,
                    "display_name": role.display_name,
                    "round": round_name,
                    "content": f"{role.display_name}: {round_name} 발언",
                    "position": "조건부",
                    "stance_status": "held",
                    "change_conditions": [],
                    "confidence": "medium",
                }

            def synthesize(self, session, question, public_context):
                return {
                    "winner": "Undetermined",
                    "ranking": [],
                    "confidence": "low",
                    "caveats": ["one role failed research"],
                    "summary": "일부 역할 실패로 결론을 보류합니다.",
                    "tasks": {},
                }

        def registry_with_failing_adapter(*args, **kwargs):
            registry = default_provider_registry(*args, **kwargs)
            registry.register(
                "one_role_failing",
                lambda _provider: OneRoleFailingAdapter(),
                ProviderCapabilities(
                    supports_research=True,
                    supports_web_search=False,
                    supports_tools=False,
                    supports_filesystem=False,
                    supports_session_resume=False,
                    supports_structured_output=True,
                ),
            )
            return registry

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            agent_config = root / "agents.json"
            agent_config.write_text(
                """
{
  "providers": [{"id": "failing", "kind": "one_role_failing", "display_name": "Failing"}],
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
            with patch("agentsassemble.meeting_setup.default_provider_registry", registry_with_failing_adapter):
                result = run_demo_meeting(adapter_name="mock", output_root=root, agent_config_path=agent_config)
            meeting = __import__("json").loads((result.meeting_dir / "meeting.json").read_text(encoding="utf-8"))
            failed_research_path = result.meeting_dir / "private_research" / "show_me_the_feats" / "research.json"
            failed_packet_path = result.meeting_dir / "return_packets" / "show_me_the_feats.json"
            self.assertTrue(failed_research_path.exists())
            self.assertTrue(failed_packet_path.exists())
            failed_research = __import__("json").loads(failed_research_path.read_text(encoding="utf-8"))
            failed_packet = __import__("json").loads(failed_packet_path.read_text(encoding="utf-8"))

        failed_summary = meeting["memory_input"]["research_summaries"][1]
        self.assertEqual(failed_summary["role_id"], "show_me_the_feats")
        self.assertEqual(failed_summary["retry"]["status"], "failed")
        self.assertEqual(failed_summary["retry"]["attempts"], 2)
        self.assertEqual(failed_research["status"], "failed")
        self.assertEqual(failed_packet["research_status"], "failed")
        self.assertIn("Redo failed research before making implementation decisions.", failed_packet["handoff_checklist"])
        self.assertEqual(failed_summary["evidence_gate"]["failures"], ["research_failed"])
        self.assertEqual(meeting["evidence_gate"]["status"], "warn")
        self.assertEqual(meeting["decision_status"]["status"], "partial")


if __name__ == "__main__":
    unittest.main()
