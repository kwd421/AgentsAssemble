import subprocess
import tempfile
import unittest
from pathlib import Path

from agentsassemble.live_agent_continuity_proof import (
    fixed_continuity_code_factory,
    run_live_agent_continuity_proof,
    run_live_agent_continuity_proof_batch,
)
from agentsassemble.live_agent_runner import ResidentAgentConfig


def config(**overrides):
    values = {
        "server": "http://room.local",
        "agent_id": "agent-a",
        "display_name": "Agent A",
        "provider_kind": "kiro_live_session",
        "connection_kind": "live_session",
        "session_id": "",
        "endpoint": "",
        "auth_ref": "",
        "meeting_id": "",
        "engagement_mode": "always",
        "command": ["kiro", "chat", "--no-interactive", "--wrap", "never"],
        "timeout_seconds": 60,
        "poll_interval": 1.0,
        "heartbeat_interval": 10.0,
        "cooldown": 0.0,
        "max_chain_depth": 1,
        "max_ticks": 1,
    }
    values.update(overrides)
    return ResidentAgentConfig(**values)


class LiveAgentContinuityProofTests(unittest.TestCase):
    def test_unapproved_real_provider_proof_does_not_call_command_runner(self):
        calls = []

        result = run_live_agent_continuity_proof(
            config(),
            approve_real_providers=False,
            command_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
            code_factory=fixed_continuity_code_factory("KCODE-ABCDE12345"),
        )

        self.assertEqual(result["status"], "approval_required")
        self.assertTrue(result["approval_required"])
        self.assertFalse(result["approved"])
        self.assertIn("two_turn_provider_resume_recall_only", result["limitations"])
        self.assertEqual(calls, [])

    def test_kiro_continuity_proof_uses_resume_without_replaying_code(self):
        calls = []
        session_id = "b83e983c-6230-4700-8309-010b87583a6b"

        def command_runner(command, **kwargs):
            calls.append({"command": command, "kwargs": kwargs})
            if "--list-sessions" in command:
                stdout = "" if len([call for call in calls if "--list-sessions" in call["command"]]) == 1 else f"Chat SessionId: {session_id}\n"
                return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
            if "--resume-id" in command:
                return subprocess.CompletedProcess(command, 0, stdout="2345\n", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="READY\n", stderr="")

        result = run_live_agent_continuity_proof(
            config(),
            approve_real_providers=True,
            command_runner=command_runner,
            code_factory=fixed_continuity_code_factory("KCODE-ABCDE12345"),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["provider_kind"], "kiro_live_session")
        self.assertTrue(result["session_id_captured"])
        self.assertEqual(result["session_id_suffix"], "583a6b")
        self.assertFalse(result["second_prompt_replayed_code"])
        self.assertTrue(result["expected_suffix_matched"])
        self.assertFalse(result["first_reply_revealed_code"])
        self.assertIn("does_not_prove_room_admission", result["limitations"])
        self.assertNotIn("KCODE-ABCDE12345", str(result))
        chat_calls = [call for call in calls if "--list-sessions" not in call["command"]]
        self.assertIn("KCODE-ABCDE12345", " ".join(chat_calls[0]["command"]))
        self.assertNotIn("KCODE-ABCDE12345", " ".join(chat_calls[1]["command"]))

    def test_codex_continuity_proof_uses_resume_without_replaying_code(self):
        calls = []
        session_id = "019e3038-39cc-76a2-a746-5ba8c0f3b408"

        def command_runner(command, **kwargs):
            calls.append({"command": command, "kwargs": kwargs})
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("2345" if "resume" in command else "READY", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=f"session id: {session_id}\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_live_agent_continuity_proof(
                config(provider_kind="codex_live_session", command=["codex"]),
                approve_real_providers=True,
                command_runner=command_runner,
                code_factory=fixed_continuity_code_factory("KCODE-ABCDE12345"),
                cwd=Path(temp_dir),
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["provider_kind"], "codex_live_session")
        self.assertTrue(result["session_id_captured"])
        self.assertEqual(result["session_id_suffix"], "f3b408")
        self.assertFalse(result["second_prompt_replayed_code"])
        self.assertTrue(result["expected_suffix_matched"])
        self.assertNotIn("KCODE-ABCDE12345", str(result))
        self.assertIn("KCODE-ABCDE12345", calls[0]["kwargs"]["input"])
        self.assertNotIn("KCODE-ABCDE12345", calls[1]["kwargs"]["input"])

    def test_unsupported_provider_reports_without_calling_provider(self):
        calls = []

        result = run_live_agent_continuity_proof(
            config(provider_kind="local_cli", connection_kind="local_cli", command=["fake-agent"]),
            approve_real_providers=True,
            command_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["reason"], "provider_resume_not_supported")
        self.assertEqual(calls, [])

    def test_provider_kind_is_normalized_before_runner_setup_errors_escape(self):
        calls = []
        session_id = "019e3038-39cc-76a2-a746-5ba8c0f3b408"

        def command_runner(command, **kwargs):
            calls.append({"command": command, "kwargs": kwargs})
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("2345" if "resume" in command else "READY", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=f"session id: {session_id}\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_live_agent_continuity_proof(
                config(provider_kind="codex_live_session ", command=["codex"]),
                approve_real_providers=True,
                command_runner=command_runner,
                code_factory=fixed_continuity_code_factory("KCODE-ABCDE12345"),
                cwd=Path(temp_dir),
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["provider_kind"], "codex_live_session")
        self.assertNotIn("KCODE-ABCDE12345", str(result))

    def test_batch_reports_unsupported_without_calling_provider(self):
        calls = []

        result = run_live_agent_continuity_proof_batch(
            [config(provider_kind="grok_build_cli", connection_kind="terminal_session", command=["grok"])],
            approve_real_providers=True,
            command_runner_factory=lambda item: calls.append(item),
        )

        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["unsupported_count"], 1)
        self.assertEqual(calls, [])
        self.assertEqual(result["results"][0]["reason"], "provider_resume_not_supported")

    def test_batch_requires_approval_for_supported_providers_before_calling_provider(self):
        calls = []

        result = run_live_agent_continuity_proof_batch(
            [
                config(agent_id="kiro-a", provider_kind="kiro_live_session", command=["kiro"]),
                config(agent_id="cursor-a", provider_kind="cursor", connection_kind="terminal_session", command=["cursor-agent"]),
            ],
            approve_real_providers=False,
            command_runner_factory=lambda item: calls.append(item),
        )

        self.assertEqual(result["status"], "approval_required")
        self.assertEqual(result["approval_required_count"], 1)
        self.assertEqual(result["unsupported_count"], 1)
        self.assertEqual(calls, [])
        self.assertEqual(result["results"][0]["status"], "approval_required")
        self.assertEqual(result["results"][1]["status"], "unsupported")

    def test_batch_runs_supported_items_and_keeps_unsupported_items_safe(self):
        calls = []
        session_id = "019e3038-39cc-76a2-a746-5ba8c0f3b408"

        def command_runner_factory(item):
            self.assertEqual(item.provider_kind, "codex_live_session")

            def command_runner(command, **kwargs):
                calls.append({"command": command, "kwargs": kwargs})
                output_path = Path(command[command.index("--output-last-message") + 1])
                output_path.write_text("2345" if "resume" in command else "READY", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout=f"session id: {session_id}\n", stderr="")

            return command_runner

        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_live_agent_continuity_proof_batch(
                [
                    config(agent_id="codex-a", provider_kind="codex_live_session", command=["codex"]),
                    config(agent_id="hermes-a", provider_kind="hermes_cli", connection_kind="terminal_session", command=["hermes"]),
                ],
                approve_real_providers=True,
                command_runner_factory=command_runner_factory,
                code_factory=fixed_continuity_code_factory("KCODE-ABCDE12345"),
                cwd=Path(temp_dir),
            )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["ok_count"], 1)
        self.assertEqual(result["unsupported_count"], 1)
        self.assertEqual(result["results"][0]["status"], "ok")
        self.assertEqual(result["results"][1]["status"], "unsupported")
        self.assertNotIn("KCODE-ABCDE12345", str(result))
        self.assertEqual(len(calls), 2)

    def test_batch_rejects_supported_provider_with_wrong_executable_before_calling_provider(self):
        calls = []

        result = run_live_agent_continuity_proof_batch(
            [config(provider_kind="codex_live_session", command=["claude"])],
            approve_real_providers=True,
            command_runner_factory=lambda item: calls.append(item),
            code_factory=fixed_continuity_code_factory("KCODE-ABCDE12345"),
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["results"][0]["reason"], "resident_setup_failed")
        self.assertEqual(calls, [])
        self.assertNotIn("KCODE-ABCDE12345", str(result))

    def test_batch_uses_setup_error_checker_before_calling_provider(self):
        calls = []

        result = run_live_agent_continuity_proof_batch(
            [config(provider_kind="kiro_live_session", command=["kiro"])],
            approve_real_providers=True,
            setup_error_checker=lambda item: "resident_setup_failed",
            command_runner_factory=lambda item: calls.append(item),
            code_factory=fixed_continuity_code_factory("KCODE-ABCDE12345"),
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["results"][0]["reason"], "resident_setup_failed")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
