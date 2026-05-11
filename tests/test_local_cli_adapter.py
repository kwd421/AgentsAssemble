import subprocess
import unittest
import json

from agentsassemble.adapters.local_cli import LocalCliAdapter, LocalCliError
from agentsassemble.models import ProviderConfig, ResearchSteering, Role, get_research_depth


class LocalCliAdapterTests(unittest.TestCase):
    def test_local_cli_research_invokes_configured_command_with_read_only_prompt(self):
        calls = []

        def runner(command, input, text, capture_output, timeout, check):
            calls.append({"command": command, "input": input, "timeout": timeout})
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"queries":["q"],"sources":[],"summary":"조사 완료","confidence":"medium",'
                    '"uncertainty":"","claim_evidence":[],"counterclaims":[],"rejected_claims":[]}'
                ),
                stderr="",
            )

        adapter = LocalCliAdapter(
            ProviderConfig(
                id="gemini-cli",
                kind="local_cli",
                display_name="Gemini CLI",
                command=["gemini", "--prompt"],
                timeout_seconds=30,
            ),
            command_runner=runner,
        )
        role = Role("scout", "정찰병", "Research scout", "자료 조사")
        session = adapter.start_session(role, {"meeting_id": "m1"})

        research = adapter.run_research(role, session, "고릴라 1마리 vs 헬창 100명?", get_research_depth("smoke"), ResearchSteering())

        self.assertEqual(calls[0]["command"], ["gemini", "--prompt"])
        self.assertEqual(calls[0]["timeout"], 30)
        self.assertIn("Do not run shell commands", calls[0]["input"])
        self.assertIn("Return only JSON", calls[0]["input"])
        self.assertEqual(research["summary"], "조사 완료")
        self.assertEqual(research["provider"]["kind"], "local_cli")
        self.assertEqual(session["permissions"]["mode"], "meeting_read_only")
        self.assertFalse(session["permissions"]["filesystem_write"])
        self.assertFalse(session["permissions"]["git_write"])

    def test_local_cli_provider_metadata_redacts_command_arguments(self):
        def runner(command, input, text, capture_output, timeout, check):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"queries":[],"sources":[],"summary":"ok","confidence":"medium","uncertainty":"","claim_evidence":[],"counterclaims":[],"rejected_claims":[]}',
                stderr="",
            )

        adapter = LocalCliAdapter(
            ProviderConfig(
                id="secret-cli",
                kind="local_cli",
                display_name="Secret CLI",
                command=["agent", "--api-key", "secret-token"],
            ),
            command_runner=runner,
        )
        role = Role("role", "역할", "Lens", "focus")

        research = adapter.run_research(
            role,
            adapter.start_session(role, {"meeting_id": "m5"}),
            "question",
            get_research_depth("smoke"),
            ResearchSteering(),
        )
        metadata = json.dumps(research["provider"], ensure_ascii=False)

        self.assertNotIn("secret-token", metadata)
        self.assertEqual(research["provider"]["command"], ["<redacted>"])

    def test_local_cli_round_parses_json_and_keeps_role_metadata(self):
        def runner(command, input, text, capture_output, timeout, check):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"content":"나는 아직 고릴라 쪽 본다.","position":"고릴라 우세",'
                    '"stance_status":"held","stance_delta":"none","changed_by":[],'
                    '"change_reason":"","remaining_resistance":"숫자만으로는 부족함",'
                    '"emotion":{"tone":"skeptical"},"change_conditions":["공간 조건"],"confidence":"medium"}'
                ),
                stderr="",
            )

        adapter = LocalCliAdapter(
            ProviderConfig(id="claude-code", kind="local_cli", display_name="Claude Code", command=["claude", "-p"]),
            command_runner=runner,
        )
        role = Role("skeptic", "태클러", "Skeptic", "반례")

        message = adapter.run_round(role, adapter.start_session(role, {"meeting_id": "m2"}), "round_1", "첫 주장", {})

        self.assertEqual(message["role_id"], "skeptic")
        self.assertEqual(message["content"], "나는 아직 고릴라 쪽 본다.")
        self.assertEqual(message["stance_status"], "held")
        self.assertEqual(message["emotion"]["tone"], "skeptical")
        self.assertEqual(message["provider"]["display_name"], "Claude Code")

    def test_local_cli_requires_explicit_command(self):
        adapter = LocalCliAdapter(ProviderConfig(id="missing", kind="local_cli", display_name="Missing"))
        role = Role("role", "역할", "Lens", "focus")

        with self.assertRaisesRegex(ValueError, "requires command"):
            adapter.run_round(role, adapter.start_session(role, {"meeting_id": "m3"}), "round_1", "발언", {})

    def test_local_cli_non_zero_exit_raises_for_retry_gate(self):
        def runner(command, input, text, capture_output, timeout, check):
            return subprocess.CompletedProcess(command, 7, stdout="", stderr="boom")

        adapter = LocalCliAdapter(
            ProviderConfig(id="bad-cli", kind="local_cli", display_name="Bad CLI", command=["bad-cli"]),
            command_runner=runner,
        )
        role = Role("role", "역할", "Lens", "focus")

        with self.assertRaises(LocalCliError) as caught:
            adapter.run_research(role, adapter.start_session(role, {"meeting_id": "m4"}), "question", get_research_depth("smoke"), ResearchSteering())

        self.assertEqual(caught.exception.returncode, 7)
        self.assertIn("boom", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
