import subprocess
import tempfile
import unittest
from pathlib import Path

from agentsassemble.live_agent_runner import ResidentAgentConfig
from agentsassemble.providers.codex_resident import (
    CODEX_LOGIN_REQUIRED_MESSAGE,
    CodexResidentCommandRunner,
    codex_auth_check,
)


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
    def test_streaming_turn_drains_large_stderr_while_reading_stdout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            executable = temp_path / "fake-codex"
            executable.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "import sys\n"
                "sys.stderr.write('diagnostic line\\n' * 100000)\n"
                "sys.stderr.flush()\n"
                "print(json.dumps({'type': 'item.completed', "
                "'item': {'type': 'agent_message', 'text': 'current streamed reply'}}), flush=True)\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            runner = CodexResidentCommandRunner(
                _codex_config(command=[str(executable)], stream_thinking=True),
                cwd=temp_path,
            )
            try:
                reply = runner([], "prompt", timeout_seconds=1)
            finally:
                runner.close()

        self.assertEqual(reply, "current streamed reply")

    def test_streaming_turn_preserves_auth_error_before_large_stderr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            executable = temp_path / "fake-codex"
            executable.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "sys.stderr.write('not authenticated\\n')\n"
                "sys.stderr.write('diagnostic line\\n' * 100000)\n"
                "sys.stderr.flush()\n"
                "raise SystemExit(1)\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            runner = CodexResidentCommandRunner(
                _codex_config(command=[str(executable)], stream_thinking=True),
                cwd=temp_path,
            )
            try:
                with self.assertRaisesRegex(RuntimeError, CODEX_LOGIN_REQUIRED_MESSAGE):
                    runner([], "prompt", timeout_seconds=1)
            finally:
                runner.close()

    def test_repeated_turn_does_not_reuse_previous_output_file(self):
        calls = 0

        def command_runner(command, **kwargs):
            nonlocal calls
            calls += 1
            output_path = Path(command[command.index("--output-last-message") + 1])
            if calls == 1:
                output_path.write_text("previous reply", encoding="utf-8")
                stdout = ""
            else:
                stdout = "current reply"
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        runner = CodexResidentCommandRunner(
            _codex_config(),
            command_runner=command_runner,
        )
        try:
            first = runner([], "first", timeout_seconds=5)
            second = runner([], "second", timeout_seconds=5)
        finally:
            runner.close()

        self.assertEqual(first, "previous reply")
        self.assertEqual(second, "current reply")

    def test_streaming_turn_rejects_previous_output_when_current_turn_is_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            state_path = temp_path / "called"
            executable = temp_path / "fake-codex"
            executable.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib\n"
                "import sys\n"
                f"state = pathlib.Path({str(state_path)!r})\n"
                "output = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
                "if not state.exists():\n"
                "    state.write_text('called', encoding='utf-8')\n"
                "    output.write_text('previous streamed reply', encoding='utf-8')\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            runner = CodexResidentCommandRunner(
                _codex_config(command=[str(executable)], stream_thinking=True),
                cwd=temp_path,
            )
            try:
                first = runner([], "first", timeout_seconds=5)
                with self.assertRaisesRegex(ValueError, "empty reply"):
                    runner([], "second", timeout_seconds=5)
            finally:
                runner.close()

        self.assertEqual(first, "previous streamed reply")

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
