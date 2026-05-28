import subprocess
import tempfile
import unittest
from pathlib import Path

from agentsassemble.antigravity_resident import (
    ANTIGRAVITY_EMPTY_REPLY,
    ANTIGRAVITY_MISSING_CONVERSATION_ID,
    ANTIGRAVITY_SUBPROCESS_NONZERO,
    AntigravityResidentCommandRunner,
    antigravity_command_check,
    antigravity_error_category,
    antigravity_provider_connection_check,
    clean_antigravity_conversation_id,
    default_antigravity_resident_command,
)
from agentsassemble.live_agent_runner import ResidentAgentConfig


def config(**overrides):
    values = {
        "server": "http://room.local",
        "agent_id": "antigravity-a",
        "display_name": "Antigravity A",
        "provider_kind": "antigravity_live_session",
        "connection_kind": "live_session",
        "session_id": "",
        "endpoint": "",
        "auth_ref": "",
        "meeting_id": "",
        "engagement_mode": "always",
        "command": ["agy"],
        "timeout_seconds": 60,
        "poll_interval": 1.0,
        "heartbeat_interval": 10.0,
        "cooldown": 0.0,
        "max_chain_depth": 1,
        "max_ticks": 1,
    }
    values.update(overrides)
    return ResidentAgentConfig(**values)


class AntigravityResidentTests(unittest.TestCase):
    def test_runner_captures_created_conversation_then_resumes_it(self):
        calls = []
        conversation_id = "a" * 36

        def command_runner(command, **kwargs):
            calls.append({"command": command, "kwargs": kwargs})
            if "--log-file" in command:
                log_path = Path(command[command.index("--log-file") + 1])
                log_path.write_text(f"Created conversation {conversation_id}\n", encoding="utf-8")
            if "--conversation" in command:
                self.assertIn(conversation_id, command)
                self.assertNotIn("SECRET-CODE", " ".join(command))
                return subprocess.CompletedProcess(command, 0, stdout="The suffix is C123.", stderr="SECRET-CODE")
            self.assertIn("SECRET-CODE", " ".join(command))
            return subprocess.CompletedProcess(command, 0, stdout="READY\n", stderr="SECRET-CODE")

        with tempfile.TemporaryDirectory() as temp_dir:
            runner = AntigravityResidentCommandRunner(config(), command_runner=command_runner, cwd=Path(temp_dir))
            try:
                first = runner([], "store SECRET-CODE", timeout_seconds=45)
                second = runner([], "suffix only", timeout_seconds=45)
            finally:
                runner.close()

        self.assertEqual(first, "READY")
        self.assertEqual(second, "The suffix is C123.")
        self.assertEqual(runner.session_id, conversation_id)
        self.assertEqual(calls[0]["command"][0], "agy")
        self.assertIn("--print", calls[0]["command"])
        self.assertIn("--conversation", calls[1]["command"])
        self.assertEqual(calls[0]["kwargs"]["cwd"], str(Path(temp_dir)))

    def test_runner_reports_safe_failures(self):
        def no_conversation(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="READY", stderr="")

        runner = AntigravityResidentCommandRunner(config(), command_runner=no_conversation, cwd=Path.cwd())
        try:
            with self.assertRaisesRegex(ValueError, "safe conversation id") as missing:
                runner([], "prompt", timeout_seconds=45)
        finally:
            runner.close()
        self.assertEqual(antigravity_error_category(missing.exception), ANTIGRAVITY_MISSING_CONVERSATION_ID)

        def nonzero(command, **kwargs):
            return subprocess.CompletedProcess(command, 7, stdout="", stderr="SECRET")

        failed = AntigravityResidentCommandRunner(
            config(session_id="a" * 36),
            command_runner=nonzero,
            cwd=Path.cwd(),
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "return code 7") as caught:
                failed([], "prompt", timeout_seconds=45)
        finally:
            failed.close()
        self.assertEqual(antigravity_error_category(caught.exception), ANTIGRAVITY_SUBPROCESS_NONZERO)

        def empty(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="   ", stderr="")

        empty_runner = AntigravityResidentCommandRunner(
            config(session_id="a" * 36),
            command_runner=empty,
            cwd=Path.cwd(),
        )
        try:
            with self.assertRaisesRegex(ValueError, "empty reply") as empty_error:
                empty_runner([], "prompt", timeout_seconds=45)
        finally:
            empty_runner.close()
        self.assertEqual(antigravity_error_category(empty_error.exception), ANTIGRAVITY_EMPTY_REPLY)

    def test_provider_checks_and_defaults_are_narrow(self):
        self.assertEqual(default_antigravity_resident_command("antigravity_live_session", "live_session", []), ["agy"])
        self.assertEqual(default_antigravity_resident_command("antigravity_cli", "live_session", []), [])
        self.assertEqual(
            antigravity_provider_connection_check("antigravity_live_session", "live_session"),
            {
                "id": "provider_connection_kind",
                "status": "ok",
                "message": "antigravity_live_session uses live_session.",
            },
        )
        self.assertEqual(antigravity_provider_connection_check("antigravity_cli", "self_service"), None)
        self.assertEqual(antigravity_command_check(["agy"])["status"], "ok")
        self.assertEqual(antigravity_command_check(["antigravity"])["status"], "ok")
        self.assertEqual(antigravity_command_check(["agy", "--continue"])["status"], "failed")
        self.assertEqual(antigravity_command_check(["hermes"])["status"], "failed")
        self.assertEqual(clean_antigravity_conversation_id("unsafe id"), "")


if __name__ == "__main__":
    unittest.main()
