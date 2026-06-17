import subprocess
import tempfile
import unittest
from pathlib import Path

from agentsassemble.codex_resident import CodexResidentCommandRunner, codex_auth_check
from agentsassemble.live_agent_runner import ResidentAgentConfig


def _codex_config(**overrides) -> ResidentAgentConfig:
    base = dict(
        server="http://room.local",
        agent_id="codex-1",
        display_name="Codex",
        provider_kind="codex_live_session",
        connection_kind="live_session",
        session_id="",
        endpoint="",
        auth_ref="",
        meeting_id="",
        engagement_mode="mentioned",
        command=["codex"],
        timeout_seconds=5,
        poll_interval=0.05,
        heartbeat_interval=0.0,
        cooldown=0.0,
        max_chain_depth=0,
    )
    base.update(overrides)
    return ResidentAgentConfig(**base)


class CodexResidentFastModeTests(unittest.TestCase):
    def test_build_command_enables_fast_mode_when_toggled(self):
        runner = CodexResidentCommandRunner(_codex_config(fast_mode=True))
        command = runner._build_command(Path(tempfile.gettempdir()) / "out.txt")
        self.assertIn("--enable", command)
        self.assertEqual(command[command.index("--enable") + 1], "fast_mode")
        runner.close()

    def test_build_command_omits_fast_mode_by_default(self):
        runner = CodexResidentCommandRunner(_codex_config())
        command = runner._build_command(Path(tempfile.gettempdir()) / "out.txt")
        self.assertNotIn("--enable", command)
        runner.close()

    def test_runtime_overrides_apply_live_without_restart(self):
        # Config says read-only + fast off; a live edit flips both for the next turn.
        runner = CodexResidentCommandRunner(_codex_config(permission_option="read-only"))
        out = Path(tempfile.gettempdir()) / "out.txt"
        runner.apply_runtime_overrides(permission_option="danger-full-access", fast_mode=True)
        command = runner._build_command(out)
        self.assertIn("danger-full-access", command)
        self.assertIn("--enable", command)
        self.assertEqual(command[command.index("--enable") + 1], "fast_mode")
        # Flipping back is also live.
        runner.apply_runtime_overrides(permission_option="read-only", fast_mode=False)
        reverted = runner._build_command(out)
        self.assertIn("read-only", reverted)
        self.assertNotIn("--enable", reverted)
        runner.close()


class CodexResidentAuthTests(unittest.TestCase):
    def test_auth_check_reports_login_required_and_accepts_login_status(self):
        def authenticated(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="Logged in using ChatGPT")

        self.assertEqual(codex_auth_check(["codex"], command_runner=authenticated)["status"], "ok")

        def unauthenticated(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not authenticated")

        check = codex_auth_check(["codex"], command_runner=unauthenticated)

        self.assertEqual(check["id"], "codex_auth")
        self.assertEqual(check["status"], "failed")
        self.assertIn("Codex 로그인이 필요합니다", check["message"])


if __name__ == "__main__":
    unittest.main()
