import subprocess
import tempfile
import unittest
from pathlib import Path
from subprocess import TimeoutExpired

from agentsassemble.adapters.codex import CodexAdapter
from agentsassemble.models import Role


class CodexAdapterTests(unittest.TestCase):
    def test_codex_research_uses_output_last_message_and_records_session_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            role_dir = meeting_dir / "roles" / "lore_lawyer"
            role_dir.mkdir(parents=True)

            def fake_runner(command, input, text, capture_output, timeout, check):
                output_path = Path(command[command.index("--output-last-message") + 1])
                output_path.write_text(
                    '{"queries":["q"],"sources":[],"summary":"s","confidence":"medium","uncertainty":"u","claim_evidence":[]}',
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="session id: 019e0346-f384-74f2-914e-c95f535edf46\n",
                    stderr="",
                )

            adapter = CodexAdapter(command_runner=fake_runner)
            role = Role("lore_lawyer", "설정충", "Canon Analyst", "canon")
            session = adapter.start_session(role, {"meeting_dir": str(meeting_dir)})

            research = adapter.run_research(role, session, "Question?")

            self.assertEqual(research["summary"], "s")
            self.assertEqual(session["session_id"], "019e0346-f384-74f2-914e-c95f535edf46")
            self.assertEqual(research["codex"]["returncode"], 0)
            self.assertIn("codex", research["codex"]["command"])
            self.assertIn("--search", research["codex"]["command"])

    def test_codex_round_calls_do_not_use_search(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            (meeting_dir / "roles" / "lore_lawyer").mkdir(parents=True)
            seen_commands = []

            def fake_runner(command, input, text, capture_output, timeout, check):
                seen_commands.append(command)
                output_path = Path(command[command.index("--output-last-message") + 1])
                output_path.write_text('{"content":"c","confidence":"medium"}', encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            adapter = CodexAdapter(command_runner=fake_runner)
            role = Role("lore_lawyer", "설정충", "Canon Analyst", "canon")
            session = adapter.start_session(role, {"meeting_dir": str(meeting_dir)})

            adapter.run_round(role, session, "round_1", "Prompt", {})

            self.assertNotIn("--search", seen_commands[0])

    def test_codex_timeout_becomes_low_confidence_research(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir)
            (meeting_dir / "roles" / "lore_lawyer").mkdir(parents=True)

            def fake_timeout_runner(command, input, text, capture_output, timeout, check):
                error = TimeoutExpired(command, timeout)
                error.stderr = b"byte stderr"
                raise error

            adapter = CodexAdapter(command_runner=fake_timeout_runner, timeout_seconds=1)
            role = Role("lore_lawyer", "설정충", "Canon Analyst", "canon")
            session = adapter.start_session(role, {"meeting_dir": str(meeting_dir)})

            research = adapter.run_research(role, session, "Question?")

            self.assertEqual(research["confidence"], "low")
            self.assertTrue(research["codex"]["timed_out"])
            self.assertIsInstance(research["codex"]["stderr"], str)
            self.assertIn("timed out", research["summary"])


if __name__ == "__main__":
    unittest.main()
