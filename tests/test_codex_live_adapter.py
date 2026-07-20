import subprocess
import tempfile
import unittest
from pathlib import Path

from agentsassemble.providers.adapters.codex_live import CodexLiveSessionAdapter
from agentsassemble.models import ResearchSteering, Role, get_research_depth
from agentsassemble.providers.codex_session_ids import extract_codex_session_id


class CodexLiveSessionAdapterTests(unittest.TestCase):
    def assert_codex_exec_safety_flags(self, command):
        exec_index = command.index("exec")
        self.assertEqual(
            command[exec_index : exec_index + 5],
            ["exec", "--sandbox", "read-only", "--ignore-user-config", "--ignore-rules"],
        )
        self.assertEqual(command.count("--sandbox"), 1)
        self.assertEqual(command.count("read-only"), 1)
        self.assertEqual(command.count("--ignore-user-config"), 1)
        self.assertEqual(command.count("--ignore-rules"), 1)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)

    def test_live_session_starts_fresh_then_resumes_same_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            (meeting_dir / "roles" / "lore_lawyer").mkdir(parents=True)
            calls = []

            def fake_runner(command, input, text, capture_output, timeout, check, cwd=None):
                calls.append({"command": command, "input": input, "cwd": cwd})
                output_path = Path(command[command.index("--output-last-message") + 1])
                if len(calls) == 1:
                    output_path.write_text(
                        '{"queries":["q"],"sources":[],"summary":"조사","confidence":"medium","uncertainty":"","claim_evidence":[]}',
                        encoding="utf-8",
                    )
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout="session id: 019e02af-c287-7cd1-aab7-c1e059c5ed44\n",
                        stderr="",
                    )
                output_path.write_text('{"content":"이전 조사 보고 이어 말함","confidence":"medium"}', encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            adapter = CodexLiveSessionAdapter(command_runner=fake_runner, timeout_seconds=240)
            role = Role("lore_lawyer", "설정충", "Canon Analyst", "canon")
            session = adapter.start_session(role, {"meeting_dir": str(meeting_dir), "meeting_id": "m1"})

            research = adapter.run_research(
                role,
                session,
                "Question?",
                get_research_depth("smoke"),
                ResearchSteering(),
            )
            message = adapter.run_round(role, session, "round_1", "첫 발언", {"research": research})

            self.assertEqual(calls[0]["command"][:3], ["codex", "--search", "exec"])
            self.assert_codex_exec_safety_flags(calls[0]["command"])
            self.assertNotIn("resume", calls[0]["command"])
            self.assertEqual(session["session_id"], "019e02af-c287-7cd1-aab7-c1e059c5ed44")
            self.assertEqual(research["codex"]["session_mode"], "started")
            self.assertEqual(calls[1]["command"][:2], ["codex", "exec"])
            self.assert_codex_exec_safety_flags(calls[1]["command"])
            self.assertLess(calls[1]["command"].index("--sandbox"), calls[1]["command"].index("resume"))
            self.assertIn("019e02af-c287-7cd1-aab7-c1e059c5ed44", calls[1]["command"])
            self.assertEqual(calls[1]["cwd"], str(meeting_dir))
            self.assertEqual(message["codex"]["session_mode"], "resumed")

    def test_session_id_extractor_accepts_jsonl_and_label_variants(self):
        self.assertEqual(
            extract_codex_session_id('{"type":"session.started","session":{"id":"019e02af-c287-7cd1-aab7-c1e059c5ed44"}}\n'),
            "019e02af-c287-7cd1-aab7-c1e059c5ed44",
        )
        self.assertEqual(
            extract_codex_session_id("Session ID: 019e3038-39cc-76a2-a746-5ba8c0f3b408\n"),
            "019e3038-39cc-76a2-a746-5ba8c0f3b408",
        )
        self.assertEqual(
            extract_codex_session_id('{"msg":{"session_id":"019e0346-f384-74f2-914e-c95f535edf46"}}\n'),
            "019e0346-f384-74f2-914e-c95f535edf46",
        )

    def test_live_session_uses_configured_session_id_on_first_call(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            (meeting_dir / "roles" / "fanboard_skeptic").mkdir(parents=True)
            calls = []

            def fake_runner(command, input, text, capture_output, timeout, check, cwd=None):
                calls.append({"command": command, "cwd": cwd})
                output_path = Path(command[command.index("--output-last-message") + 1])
                output_path.write_text('{"content":"기존 세션에서 바로 참가","confidence":"medium"}', encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            adapter = CodexLiveSessionAdapter(command_runner=fake_runner)
            role = Role("fanboard_skeptic", "만갤러", "Skeptic", "반례")
            session = adapter.start_session(
                role,
                {
                    "meeting_dir": str(meeting_dir),
                    "meeting_id": "m2",
                    "session_ids": {"fanboard_skeptic": "019e3038-39cc-76a2-a746-5ba8c0f3b408"},
                },
            )

            message = adapter.run_round(role, session, "round_1", "첫 발언", {})

            self.assertEqual(calls[0]["command"][:2], ["codex", "exec"])
            self.assert_codex_exec_safety_flags(calls[0]["command"])
            self.assertLess(calls[0]["command"].index("--sandbox"), calls[0]["command"].index("resume"))
            self.assertIn("019e3038-39cc-76a2-a746-5ba8c0f3b408", calls[0]["command"])
            self.assertEqual(session["session_id"], "019e3038-39cc-76a2-a746-5ba8c0f3b408")
            self.assertEqual(message["codex"]["session_mode"], "resumed")


if __name__ == "__main__":
    unittest.main()
