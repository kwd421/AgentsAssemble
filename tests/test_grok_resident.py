import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from agentsassemble.grok_resident import (
    GROK_EMPTY_TEXT,
    GROK_JSON_PARSE_FAILURE,
    GROK_MISSING_SESSION_ID,
    GROK_SUBPROCESS_NONZERO,
    GROK_SUBPROCESS_TIMEOUT,
    GrokResidentCommandRunner,
    clean_grok_session_id,
    default_grok_resident_command,
    grok_command_check,
    grok_error_category,
    grok_provider_connection_check,
)
from agentsassemble.live_agent_runner import ResidentAgentConfig


def config(**overrides):
    values = {
        "server": "http://room.local",
        "agent_id": "grok-a",
        "display_name": "Grok A",
        "provider_kind": "grok_live_session",
        "connection_kind": "live_session",
        "session_id": "",
        "endpoint": "",
        "auth_ref": "",
        "meeting_id": "",
        "engagement_mode": "always",
        "command": ["grok"],
        "timeout_seconds": 60,
        "poll_interval": 1.0,
        "heartbeat_interval": 10.0,
        "cooldown": 0.0,
        "max_chain_depth": 1,
        "max_ticks": 1,
    }
    values.update(overrides)
    return ResidentAgentConfig(**values)


class GrokResidentTests(unittest.TestCase):
    def test_runner_starts_with_prompt_file_then_resumes_session(self):
        calls = []
        session_id = "grok-session-abc123"

        def command_runner(command, **kwargs):
            calls.append({"command": command, "kwargs": kwargs})
            prompt_path = Path(command[command.index("--prompt-file") + 1])
            prompt = prompt_path.read_text(encoding="utf-8")
            if "--resume" in command:
                self.assertNotIn("SECRET-CODE", prompt)
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps({"sessionId": session_id, "text": "C123"}),
                    stderr="prompt echo SECRET-CODE should be ignored",
                )
            self.assertIn("SECRET-CODE", prompt)
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"sessionId": session_id, "text": "READY"}),
                stderr="prompt echo SECRET-CODE should be ignored",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            runner = GrokResidentCommandRunner(
                config(command=["grok", "--always-approve"]),
                command_runner=command_runner,
                cwd=Path(temp_dir),
            )
            try:
                first = runner([], "store SECRET-CODE", timeout_seconds=45)
                second = runner([], "suffix only", timeout_seconds=45)
            finally:
                runner.close()

        self.assertEqual(first, "READY")
        self.assertEqual(second, "C123")
        self.assertEqual(runner.session_id, session_id)
        self.assertEqual(calls[0]["command"][:6], ["grok", "--prompt-file", calls[0]["command"][2], "--output-format", "json", "--disable-web-search"])
        self.assertIn("--no-subagents", calls[0]["command"])
        self.assertIn("--verbatim", calls[0]["command"])
        self.assertNotIn("--always-approve", calls[0]["command"])
        self.assertNotIn("SECRET-CODE", " ".join(calls[0]["command"]))
        self.assertIn("--resume", calls[1]["command"])
        self.assertIn(session_id, calls[1]["command"])
        self.assertEqual(calls[0]["kwargs"]["cwd"], str(Path(temp_dir)))

    def test_runner_rejects_invalid_json_without_leaking_output(self):
        def command_runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="not json SECRET-CODE", stderr="SECRET-CODE")

        runner = GrokResidentCommandRunner(config(), command_runner=command_runner, cwd=Path.cwd())
        try:
            with self.assertRaisesRegex(ValueError, "invalid JSON stdout") as caught:
                runner([], "SECRET-CODE", timeout_seconds=45)
        finally:
            runner.close()
        self.assertEqual(grok_error_category(caught.exception), GROK_JSON_PARSE_FAILURE)

    def test_runner_requires_safe_session_id_for_fresh_session(self):
        def command_runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"sessionId": "unsafe id with spaces", "text": "READY"}), stderr="")

        runner = GrokResidentCommandRunner(config(), command_runner=command_runner, cwd=Path.cwd())
        try:
            with self.assertRaisesRegex(ValueError, "safe session id") as caught:
                runner([], "prompt", timeout_seconds=45)
        finally:
            runner.close()
        self.assertEqual(grok_error_category(caught.exception), GROK_MISSING_SESSION_ID)

    def test_runner_treats_nonzero_and_timeout_as_safe_errors(self):
        def failed_runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 2, stdout="", stderr="token=SECRET")

        failed = GrokResidentCommandRunner(config(), command_runner=failed_runner, cwd=Path.cwd())
        try:
            with self.assertRaisesRegex(RuntimeError, "return code 2") as failed_error:
                failed([], "prompt", timeout_seconds=45)
        finally:
            failed.close()
        self.assertEqual(grok_error_category(failed_error.exception), GROK_SUBPROCESS_NONZERO)

        def timeout_runner(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        timed_out = GrokResidentCommandRunner(config(), command_runner=timeout_runner, cwd=Path.cwd())
        try:
            with self.assertRaisesRegex(RuntimeError, "timed out after 45 seconds") as timeout_error:
                timed_out([], "prompt", timeout_seconds=45)
        finally:
            timed_out.close()
        self.assertEqual(grok_error_category(timeout_error.exception), GROK_SUBPROCESS_TIMEOUT)

    def test_runner_rejects_missing_text(self):
        def command_runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"sessionId": "session-1"}), stderr="")

        runner = GrokResidentCommandRunner(config(), command_runner=command_runner, cwd=Path.cwd())
        try:
            with self.assertRaisesRegex(ValueError, "empty JSON text reply") as caught:
                runner([], "prompt", timeout_seconds=45)
        finally:
            runner.close()
        self.assertEqual(grok_error_category(caught.exception), GROK_EMPTY_TEXT)

    def test_provider_checks_and_defaults_are_narrow(self):
        self.assertEqual(default_grok_resident_command("grok_live_session", "live_session", []), ["grok"])
        self.assertEqual(default_grok_resident_command("local_cli", "live_session", []), [])
        self.assertEqual(
            grok_provider_connection_check("grok_live_session", "live_session"),
            {
                "id": "provider_connection_kind",
                "status": "ok",
                "message": "grok_live_session uses live_session.",
            },
        )
        self.assertEqual(grok_provider_connection_check("local_cli", "live_session"), None)
        self.assertEqual(grok_command_check(["grok"])["status"], "ok")
        self.assertEqual(grok_command_check(["grok", "--always-approve"])["status"], "failed")
        self.assertEqual(grok_command_check(["agy"])["status"], "failed")
        self.assertEqual(clean_grok_session_id("unsafe id"), "")


if __name__ == "__main__":
    unittest.main()
