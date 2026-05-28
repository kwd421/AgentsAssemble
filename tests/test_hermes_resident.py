import subprocess
import unittest
from pathlib import Path

from agentsassemble.hermes_resident import (
    HERMES_EMPTY_REPLY,
    HERMES_MISSING_SESSION_ID,
    HERMES_SUBPROCESS_NONZERO,
    HermesResidentCommandRunner,
    clean_hermes_session_id,
    default_hermes_resident_command,
    hermes_command_check,
    hermes_error_category,
    hermes_provider_connection_check,
)
from agentsassemble.live_agent_runner import ResidentAgentConfig


def config(**overrides):
    values = {
        "server": "http://room.local",
        "agent_id": "hermes-a",
        "display_name": "Hermes A",
        "provider_kind": "hermes_live_session",
        "connection_kind": "live_session",
        "session_id": "",
        "endpoint": "",
        "auth_ref": "",
        "meeting_id": "resident-m1",
        "engagement_mode": "always",
        "command": ["hermes"],
        "timeout_seconds": 60,
        "poll_interval": 1.0,
        "heartbeat_interval": 10.0,
        "cooldown": 0.0,
        "max_chain_depth": 1,
        "max_ticks": 1,
    }
    values.update(overrides)
    return ResidentAgentConfig(**values)


class HermesResidentTests(unittest.TestCase):
    def test_runner_captures_session_id_then_resumes_it(self):
        calls = []
        session_id = "hermes-session-abc123"

        def command_runner(command, **kwargs):
            calls.append({"command": command, "kwargs": kwargs})
            self.assertIn("chat", command)
            self.assertIn("--ignore-user-config", command)
            self.assertIn("--ignore-rules", command)
            self.assertIn("--source", command)
            if "--resume" in command:
                self.assertIn(session_id, command)
                self.assertNotIn("SECRET-CODE", " ".join(command))
                return subprocess.CompletedProcess(command, 0, stdout="The suffix is H123.", stderr=f"session_id: {session_id}\n")
            self.assertIn("SECRET-CODE", " ".join(command))
            return subprocess.CompletedProcess(command, 0, stdout="READY\n", stderr=f"session_id: {session_id}\n")

        runner = HermesResidentCommandRunner(config(), command_runner=command_runner, cwd=Path.cwd())
        first = runner([], "store SECRET-CODE", timeout_seconds=45)
        second = runner([], "suffix only", timeout_seconds=45)

        self.assertEqual(first, "READY")
        self.assertEqual(second, "The suffix is H123.")
        self.assertEqual(runner.session_id, session_id)
        self.assertNotIn("--resume", calls[0]["command"])
        self.assertIn("--resume", calls[1]["command"])

    def test_runner_strips_hermes_status_prefix_from_visible_reply(self):
        session_id = "hermes-session-abc123"

        def status_prefixed(command, **kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "\u21bb Resumed session 20260528_191203_85b745 (11 user messages, 22 total messages) "
                    "(clarify timed out after 120s - agent will decide) "
                    "Reached maximum iterations (1). Requesting summary... "
                    "visible reply"
                ),
                stderr=f"session_id: {session_id}\n",
            )

        runner = HermesResidentCommandRunner(config(session_id=session_id), command_runner=status_prefixed, cwd=Path.cwd())
        reply = runner([], "prompt", timeout_seconds=45)

        self.assertEqual(reply, "visible reply")

    def test_runner_reports_safe_failures(self):
        def no_session(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="READY", stderr="")

        runner = HermesResidentCommandRunner(config(), command_runner=no_session, cwd=Path.cwd())
        with self.assertRaisesRegex(ValueError, "safe session id") as missing:
            runner([], "prompt", timeout_seconds=45)
        self.assertEqual(hermes_error_category(missing.exception), HERMES_MISSING_SESSION_ID)

        def nonzero(command, **kwargs):
            return subprocess.CompletedProcess(command, 4, stdout="", stderr="SECRET")

        failed = HermesResidentCommandRunner(
            config(session_id="hermes-session-abc123"),
            command_runner=nonzero,
            cwd=Path.cwd(),
        )
        with self.assertRaisesRegex(RuntimeError, "return code 4") as caught:
            failed([], "prompt", timeout_seconds=45)
        self.assertEqual(hermes_error_category(caught.exception), HERMES_SUBPROCESS_NONZERO)

        def empty(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout=" ", stderr="session_id: hermes-session-abc123\n")

        empty_runner = HermesResidentCommandRunner(
            config(session_id="hermes-session-abc123"),
            command_runner=empty,
            cwd=Path.cwd(),
        )
        with self.assertRaisesRegex(ValueError, "empty reply") as empty_error:
            empty_runner([], "prompt", timeout_seconds=45)
        self.assertEqual(hermes_error_category(empty_error.exception), HERMES_EMPTY_REPLY)

    def test_provider_checks_and_defaults_are_narrow(self):
        self.assertEqual(default_hermes_resident_command("hermes_live_session", "live_session", []), ["hermes"])
        self.assertEqual(default_hermes_resident_command("hermes_cli", "live_session", []), [])
        self.assertEqual(
            hermes_provider_connection_check("hermes_live_session", "live_session"),
            {
                "id": "provider_connection_kind",
                "status": "ok",
                "message": "hermes_live_session uses live_session.",
            },
        )
        self.assertEqual(hermes_provider_connection_check("hermes_cli", "terminal_session"), None)
        self.assertEqual(hermes_command_check(["hermes"])["status"], "ok")
        self.assertEqual(hermes_command_check(["hermes", "--resume", "x"])["status"], "failed")
        self.assertEqual(hermes_command_check(["agy"])["status"], "failed")
        self.assertEqual(clean_hermes_session_id("unsafe id"), "")


if __name__ == "__main__":
    unittest.main()
