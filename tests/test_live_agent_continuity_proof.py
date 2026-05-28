import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from agentsassemble.antigravity_resident import ANTIGRAVITY_BACKEND_ERROR
from agentsassemble.live_agent_continuity_proof import (
    _continuity_code,
    _first_reply_ready_normalized,
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
    def _run_grok_proof_with_first_reply(self, first_reply):
        session_id = "grok-session-abc123"

        def command_runner(command, **kwargs):
            if "--resume" in command:
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"sessionId": session_id, "text": "2345"}), stderr="")
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"sessionId": session_id, "text": first_reply}), stderr="")

        return run_live_agent_continuity_proof(
            config(provider_kind="grok_live_session", command=["grok"]),
            approve_real_providers=True,
            command_runner=command_runner,
            code_factory=fixed_continuity_code_factory("KCODE-ABCDE12345"),
        )

    def test_default_continuity_code_uses_unambiguous_letter_suffix(self):
        for _ in range(20):
            suffix = _continuity_code()[-4:]
            self.assertTrue(suffix.isalpha())
            self.assertTrue(suffix.isupper())

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
        self.assertTrue(result["first_reply_is_ready"])
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
        self.assertTrue(result["first_reply_is_ready"])
        self.assertFalse(result["second_prompt_replayed_code"])
        self.assertTrue(result["expected_suffix_matched"])
        self.assertNotIn("KCODE-ABCDE12345", str(result))
        self.assertIn("KCODE-ABCDE12345", calls[0]["kwargs"]["input"])
        self.assertNotIn("KCODE-ABCDE12345", calls[1]["kwargs"]["input"])

    def test_grok_continuity_proof_uses_json_text_resume_without_replaying_code(self):
        calls = []
        session_id = "grok-session-abc123"

        def command_runner(command, **kwargs):
            calls.append({"command": command, "kwargs": kwargs})
            prompt_path = Path(command[command.index("--prompt-file") + 1])
            prompt = prompt_path.read_text(encoding="utf-8")
            if "--resume" in command:
                self.assertNotIn("KCODE-ABCDE12345", prompt)
                return subprocess.CompletedProcess(command, 0, stdout='{"sessionId":"grok-session-abc123","text":"2345"}', stderr="KCODE-ABCDE12345")
            self.assertIn("KCODE-ABCDE12345", prompt)
            return subprocess.CompletedProcess(command, 0, stdout='{"sessionId":"grok-session-abc123","text":"READY"}', stderr="KCODE-ABCDE12345")

        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_live_agent_continuity_proof(
                config(provider_kind="grok_live_session", command=["grok"]),
                approve_real_providers=True,
                command_runner=command_runner,
                code_factory=fixed_continuity_code_factory("KCODE-ABCDE12345"),
                cwd=Path(temp_dir),
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["provider_kind"], "grok_live_session")
        self.assertTrue(result["session_id_captured"])
        self.assertEqual(result["session_id_suffix"], "abc123")
        self.assertTrue(result["first_reply_is_ready"])
        self.assertFalse(result["second_prompt_replayed_code"])
        self.assertTrue(result["expected_suffix_matched"])
        self.assertFalse(result["first_reply_revealed_code"])
        self.assertNotIn("KCODE-ABCDE12345", str(result))
        self.assertIn("--resume", calls[1]["command"])
        self.assertNotIn("KCODE-ABCDE12345", " ".join(calls[1]["command"]))

    def test_antigravity_continuity_proof_accepts_conversation_recall_with_verbose_reply(self):
        calls = []
        conversation_id = "a" * 36

        def command_runner(command, **kwargs):
            calls.append({"command": command, "kwargs": kwargs})
            if "--log-file" in command:
                log_path = Path(command[command.index("--log-file") + 1])
                log_path.write_text(f"Created conversation {conversation_id}\n", encoding="utf-8")
            if "--conversation" in command:
                self.assertNotIn("KCODE-ABCDE12345", " ".join(command))
                return subprocess.CompletedProcess(command, 0, stdout="The suffix is 2345.", stderr="KCODE-ABCDE12345")
            self.assertIn("KCODE-ABCDE12345", " ".join(command))
            return subprocess.CompletedProcess(command, 0, stdout="READY\n", stderr="KCODE-ABCDE12345")

        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_live_agent_continuity_proof(
                config(provider_kind="antigravity_live_session", command=["agy"]),
                approve_real_providers=True,
                command_runner=command_runner,
                code_factory=fixed_continuity_code_factory("KCODE-ABCDE12345"),
                cwd=Path(temp_dir),
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["provider_kind"], "antigravity_live_session")
        self.assertTrue(result["session_id_captured"])
        self.assertEqual(result["session_id_suffix"], "aaaaaa")
        self.assertFalse(result["expected_suffix_matched"])
        self.assertTrue(result["expected_suffix_recalled"])
        self.assertEqual(result["recall_match_mode"], "mentioned")
        self.assertNotIn("KCODE-ABCDE12345", str(result))
        self.assertIn("--conversation", calls[1]["command"])

    def test_antigravity_continuity_proof_uses_isolated_cwd_for_provider_sidecars(self):
        calls = []
        seen_cwds = []
        conversation_id = "b" * 36

        def command_runner(command, **kwargs):
            calls.append({"command": command, "kwargs": kwargs})
            cwd = Path(kwargs["cwd"])
            seen_cwds.append(cwd)
            (cwd / ".antigravitycli").mkdir(exist_ok=True)
            if "--log-file" in command:
                log_path = Path(command[command.index("--log-file") + 1])
                log_path.write_text(f"Created conversation {conversation_id}\n", encoding="utf-8")
            if "--conversation" in command:
                return subprocess.CompletedProcess(command, 0, stdout="The suffix is 2345.", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="READY\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_cwd = Path(temp_dir) / "repo"
            repo_cwd.mkdir()
            result = run_live_agent_continuity_proof(
                config(provider_kind="antigravity_live_session", command=["agy"]),
                approve_real_providers=True,
                command_runner=command_runner,
                code_factory=fixed_continuity_code_factory("KCODE-ABCDE12345"),
                cwd=repo_cwd,
            )

            self.assertFalse((repo_cwd / ".antigravitycli").exists())

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(calls), 2)
        self.assertTrue(seen_cwds)
        self.assertTrue(all(cwd != repo_cwd for cwd in seen_cwds))
        self.assertTrue(all(cwd.name.startswith("agentsassemble-continuity-proof-") for cwd in seen_cwds))

    def test_antigravity_continuity_proof_reports_backend_error_category(self):
        conversation_id = "c" * 36

        def command_runner(command, **kwargs):
            if "--log-file" in command:
                log_path = Path(command[command.index("--log-file") + 1])
                log_path.write_text(
                    f"Created conversation {conversation_id}\n"
                    "agent executor error: RESOURCE_EXHAUSTED (code 429): quota reached\n",
                    encoding="utf-8",
                )
            if "--conversation" in command:
                return subprocess.CompletedProcess(command, 0, stdout="previous reply", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="READY\n", stderr="")

        result = run_live_agent_continuity_proof(
            config(provider_kind="antigravity_live_session", command=["agy"]),
            approve_real_providers=True,
            command_runner=command_runner,
            code_factory=fixed_continuity_code_factory("KCODE-ABCDE12345"),
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "provider_call_failed")
        self.assertEqual(result["error_category"], ANTIGRAVITY_BACKEND_ERROR)
        self.assertIn("backend reported", result["error_message"])
        self.assertTrue(result["session_id_captured"])
        self.assertNotIn("KCODE-ABCDE12345", str(result))

    def test_hermes_continuity_proof_accepts_session_recall_with_verbose_reply(self):
        calls = []
        session_id = "hermes-session-abc123"

        def command_runner(command, **kwargs):
            calls.append({"command": command, "kwargs": kwargs})
            if "--resume" in command:
                self.assertNotIn("KCODE-ABCDE12345", " ".join(command))
                return subprocess.CompletedProcess(command, 0, stdout="The suffix is 2345.", stderr=f"session_id: {session_id}\n")
            self.assertIn("KCODE-ABCDE12345", " ".join(command))
            return subprocess.CompletedProcess(command, 0, stdout="READY. I have stored it for the next turn.", stderr=f"session_id: {session_id}\n")

        result = run_live_agent_continuity_proof(
            config(provider_kind="hermes_live_session", command=["hermes"]),
            approve_real_providers=True,
            command_runner=command_runner,
            code_factory=fixed_continuity_code_factory("KCODE-ABCDE12345"),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["provider_kind"], "hermes_live_session")
        self.assertTrue(result["session_id_captured"])
        self.assertEqual(result["session_id_suffix"], "abc123")
        self.assertFalse(result["first_reply_ready_normalized"])
        self.assertTrue(result["first_reply_ready_acknowledged"])
        self.assertFalse(result["expected_suffix_matched"])
        self.assertTrue(result["expected_suffix_recalled"])
        self.assertEqual(result["recall_match_mode"], "mentioned")
        self.assertNotIn("KCODE-ABCDE12345", str(result))
        self.assertIn("--resume", calls[1]["command"])

    def test_hermes_continuity_proof_uses_isolated_cwd(self):
        seen_cwds = []
        session_id = "hermes-session-abc123"

        def command_runner(command, **kwargs):
            seen_cwds.append(Path(kwargs["cwd"]))
            if "--resume" in command:
                return subprocess.CompletedProcess(command, 0, stdout="The suffix is 2345.", stderr=f"session_id: {session_id}\n")
            return subprocess.CompletedProcess(command, 0, stdout="READY\n", stderr=f"session_id: {session_id}\n")

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_cwd = Path(temp_dir) / "repo"
            repo_cwd.mkdir()
            result = run_live_agent_continuity_proof(
                config(provider_kind="hermes_live_session", command=["hermes"]),
                approve_real_providers=True,
                command_runner=command_runner,
                code_factory=fixed_continuity_code_factory("KCODE-ABCDE12345"),
                cwd=repo_cwd,
            )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(seen_cwds)
        self.assertTrue(all(cwd != repo_cwd for cwd in seen_cwds))
        self.assertTrue(all(cwd.name.startswith("agentsassemble-continuity-proof-") for cwd in seen_cwds))

    def test_cursor_continuity_proof_uses_chat_resume_and_stable_workspace(self):
        calls = []
        chat_id = "cursor-chat-abc123"

        def command_runner(command, **kwargs):
            calls.append({"command": command, "kwargs": kwargs})
            if command == ["cursor-agent", "create-chat"]:
                return subprocess.CompletedProcess(command, 0, stdout=f"{chat_id}\n", stderr="KCODE-ABCDE12345")
            if len([call for call in calls if "--resume" in call["command"]]) == 1:
                self.assertIn("KCODE-ABCDE12345", kwargs["input"])
                return subprocess.CompletedProcess(command, 0, stdout="READY\n", stderr="KCODE-ABCDE12345")
            self.assertNotIn("KCODE-ABCDE12345", kwargs["input"])
            return subprocess.CompletedProcess(command, 0, stdout="2345\n", stderr="KCODE-ABCDE12345")

        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_live_agent_continuity_proof(
                config(provider_kind="cursor_live_session", command=["cursor-agent"]),
                approve_real_providers=True,
                command_runner=command_runner,
                code_factory=fixed_continuity_code_factory("KCODE-ABCDE12345"),
                cwd=Path(temp_dir),
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["provider_kind"], "cursor_live_session")
        self.assertTrue(result["session_id_captured"])
        self.assertEqual(result["session_id_suffix"], "abc123")
        self.assertTrue(result["first_reply_is_ready"])
        self.assertFalse(result["second_prompt_replayed_code"])
        self.assertTrue(result["expected_suffix_matched"])
        self.assertFalse(result["first_reply_revealed_code"])
        self.assertNotIn("KCODE-ABCDE12345", str(result))
        resume_calls = [call for call in calls if "--resume" in call["command"]]
        self.assertEqual(len(resume_calls), 2)
        workspaces = [call["command"][call["command"].index("--workspace") + 1] for call in resume_calls]
        self.assertEqual(workspaces[0], workspaces[1])
        self.assertNotIn("KCODE-ABCDE12345", " ".join(resume_calls[1]["command"]))

    def test_continuity_proof_accepts_narrow_ready_marker_variants(self):
        accepted_replies = ["READY", "READY.", "READY!", "READY?", "READY\n", "  READY  ", "READY。", "READY！", "READY？"]
        for reply in accepted_replies:
            with self.subTest(reply=reply):
                result = self._run_grok_proof_with_first_reply(reply)

                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["reason"], "ok")
                self.assertEqual(result["first_reply_is_ready"], reply.strip() == "READY")
                self.assertTrue(result["first_reply_ready_normalized"])
                self.assertFalse(result["first_reply_revealed_code"])
                self.assertFalse(result["first_reply_revealed_suffix"])
                self.assertTrue(result["expected_suffix_matched"])
                self.assertNotIn("KCODE-ABCDE12345", str(result))

    def test_ready_marker_normalizer_rejects_empty_and_oversized_markers(self):
        rejected_replies = ["", "   ", "\n", "READY....", "READY-" + "x" * 32]
        for reply in rejected_replies:
            with self.subTest(reply=reply):
                self.assertFalse(_first_reply_ready_normalized(reply))

    def test_continuity_proof_rejects_extra_ready_marker_text(self):
        rejected_replies = [
            "OKAY",
            "ready",
            "Ready",
            "READYish",
            "READY because",
            "READY READY",
            "READY..",
            "READY!?",
            "READY....",
            "READY-" + "x" * 32,
        ]
        for reply in rejected_replies:
            with self.subTest(reply=reply):
                result = self._run_grok_proof_with_first_reply(reply)

                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["reason"], "first_reply_not_ready")
                self.assertFalse(result["first_reply_is_ready"])
                self.assertFalse(result["first_reply_ready_normalized"])
                self.assertFalse(result["first_reply_revealed_code"])
                self.assertFalse(result["first_reply_revealed_suffix"])
                self.assertTrue(result["expected_suffix_matched"])
                self.assertNotIn("KCODE-ABCDE12345", str(result))

    def test_continuity_proof_rejects_ready_marker_that_reveals_code_or_suffix(self):
        suffix_result = self._run_grok_proof_with_first_reply("READY 2345")
        self.assertEqual(suffix_result["status"], "failed")
        self.assertEqual(suffix_result["reason"], "first_reply_revealed_suffix")
        self.assertFalse(suffix_result["first_reply_ready_normalized"])
        self.assertTrue(suffix_result["first_reply_revealed_suffix"])
        self.assertNotIn("KCODE-ABCDE12345", str(suffix_result))

        code_result = self._run_grok_proof_with_first_reply("READY KCODE-ABCDE12345")
        self.assertEqual(code_result["status"], "failed")
        self.assertEqual(code_result["reason"], "first_reply_revealed_code")
        self.assertFalse(code_result["first_reply_ready_normalized"])
        self.assertTrue(code_result["first_reply_revealed_code"])
        self.assertNotIn("KCODE-ABCDE12345", str(code_result))

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

    def test_grok_build_cli_terminal_session_stays_unsupported(self):
        result = run_live_agent_continuity_proof(
            config(provider_kind="grok_build_cli", connection_kind="terminal_session", command=["grok"]),
            approve_real_providers=True,
            command_runner=lambda *args, **kwargs: self.fail("grok_build_cli terminal_session should not run"),
        )

        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["reason"], "provider_resume_not_supported")

    def test_single_proof_rejects_grok_live_session_with_wrong_executable_before_calling_provider(self):
        calls = []

        result = run_live_agent_continuity_proof(
            config(provider_kind="grok_live_session", command=["not-grok"]),
            approve_real_providers=True,
            command_runner=lambda *args, **kwargs: calls.append(args),
            code_factory=fixed_continuity_code_factory("KCODE-ABCDE12345"),
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "resident_setup_failed")
        self.assertTrue(result["approved"])
        self.assertEqual(calls, [])
        self.assertNotIn("KCODE-ABCDE12345", str(result))

    def test_proof_rejects_first_reply_that_reveals_suffix(self):
        session_id = "grok-session-abc123"

        def command_runner(command, **kwargs):
            if "--resume" in command:
                return subprocess.CompletedProcess(command, 0, stdout='{"sessionId":"grok-session-abc123","text":"2345"}', stderr="")
            return subprocess.CompletedProcess(command, 0, stdout='{"sessionId":"grok-session-abc123","text":"READY suffix 2345"}', stderr="")

        result = run_live_agent_continuity_proof(
            config(provider_kind="grok_live_session", command=["grok"]),
            approve_real_providers=True,
            command_runner=command_runner,
            code_factory=fixed_continuity_code_factory("KCODE-ABCDE12345"),
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "first_reply_revealed_suffix")
        self.assertTrue(result["first_reply_revealed_suffix"])
        self.assertEqual(result["session_id_suffix"], session_id[-6:])
        self.assertNotIn("KCODE-ABCDE12345", str(result))

    def test_batch_requires_approval_for_supported_providers_before_calling_provider(self):
        calls = []

        result = run_live_agent_continuity_proof_batch(
            [
                config(agent_id="kiro-a", provider_kind="kiro_live_session", command=["kiro"]),
                config(agent_id="grok-a", provider_kind="grok_live_session", command=["grok"]),
                config(agent_id="cursor-a", provider_kind="cursor_live_session", command=["cursor-agent"]),
                config(agent_id="cursor-terminal", provider_kind="cursor", connection_kind="terminal_session", command=["cursor-agent"]),
            ],
            approve_real_providers=False,
            command_runner_factory=lambda item: calls.append(item),
        )

        self.assertEqual(result["status"], "approval_required")
        self.assertEqual(result["approval_required_count"], 3)
        self.assertEqual(result["unsupported_count"], 1)
        self.assertEqual(calls, [])
        self.assertEqual(result["results"][0]["status"], "approval_required")
        self.assertEqual(result["results"][1]["status"], "approval_required")
        self.assertEqual(result["results"][2]["status"], "approval_required")
        self.assertEqual(result["results"][3]["status"], "unsupported")

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

    def test_batch_rejects_grok_live_session_with_extra_command_args_before_calling_provider(self):
        calls = []

        result = run_live_agent_continuity_proof_batch(
            [config(provider_kind="grok_live_session", command=["grok", "--always-approve"])],
            approve_real_providers=True,
            command_runner_factory=lambda item: calls.append(item),
            code_factory=fixed_continuity_code_factory("KCODE-ABCDE12345"),
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["results"][0]["reason"], "resident_setup_failed")
        self.assertEqual(calls, [])
        self.assertNotIn("KCODE-ABCDE12345", str(result))

    def test_single_proof_rejects_cursor_live_session_with_wrong_executable_before_calling_provider(self):
        calls = []

        result = run_live_agent_continuity_proof(
            config(provider_kind="cursor_live_session", command=["cursor"]),
            approve_real_providers=True,
            command_runner=lambda *args, **kwargs: calls.append(args),
            code_factory=fixed_continuity_code_factory("KCODE-ABCDE12345"),
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "resident_setup_failed")
        self.assertTrue(result["approved"])
        self.assertEqual(calls, [])
        self.assertNotIn("KCODE-ABCDE12345", str(result))


if __name__ == "__main__":
    unittest.main()
