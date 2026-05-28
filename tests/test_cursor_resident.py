import subprocess
import tempfile
import unittest
from pathlib import Path

from agentsassemble.cursor_resident import (
    CURSOR_EMPTY_TEXT,
    CURSOR_INVALID_CHAT_ID,
    CURSOR_SUBPROCESS_NONZERO,
    CURSOR_SUBPROCESS_TIMEOUT,
    CursorResidentCommandRunner,
    clean_cursor_chat_id,
    cursor_command_check,
    cursor_error_category,
    cursor_provider_connection_check,
    default_cursor_resident_command,
)
from agentsassemble.live_agent_runner import ResidentAgentConfig


def config(**overrides):
    values = {
        "server": "http://room.local",
        "agent_id": "cursor-a",
        "display_name": "Cursor A",
        "provider_kind": "cursor_live_session",
        "connection_kind": "live_session",
        "session_id": "",
        "endpoint": "",
        "auth_ref": "",
        "meeting_id": "",
        "engagement_mode": "always",
        "command": ["cursor-agent"],
        "timeout_seconds": 60,
        "poll_interval": 1.0,
        "heartbeat_interval": 10.0,
        "cooldown": 0.0,
        "max_chain_depth": 1,
        "max_ticks": 1,
    }
    values.update(overrides)
    return ResidentAgentConfig(**values)


class CursorResidentTests(unittest.TestCase):
    def test_runner_creates_chat_then_resumes_with_same_workspace(self):
        calls = []
        chat_id = "cursor-chat-abc123"

        def command_runner(command, **kwargs):
            calls.append({"command": command, "kwargs": kwargs})
            if command == ["cursor-agent", "create-chat"]:
                return subprocess.CompletedProcess(command, 0, stdout=f"{chat_id}\n", stderr="SECRET-CODE")
            if "--resume" in command:
                if len([call for call in calls if "--resume" in call["command"]]) == 1:
                    self.assertEqual(kwargs["input"], "store SECRET-CODE")
                else:
                    self.assertEqual(kwargs["input"], "suffix only")
                self.assertNotIn("SECRET-CODE", " ".join(command))
                return subprocess.CompletedProcess(command, 0, stdout="C123\n", stderr="SECRET-CODE")
            raise AssertionError(command)

        with tempfile.TemporaryDirectory() as temp_dir:
            runner = CursorResidentCommandRunner(
                config(command=["cursor-agent", "--print", "--workspace", "/tmp/foreign", "--resume", "smuggled-id"]),
                command_runner=command_runner,
                cwd=Path(temp_dir),
            )
            try:
                first = runner([], "store SECRET-CODE", timeout_seconds=45)
                second = runner([], "suffix only", timeout_seconds=45)
                workspace = runner.workspace_dir
                self.assertTrue(workspace.exists())
            finally:
                runner.close()

        self.assertEqual(first, "C123")
        self.assertEqual(second, "C123")
        self.assertEqual(runner.session_id, chat_id)
        self.assertEqual(calls[0]["command"], ["cursor-agent", "create-chat"])
        resume_calls = [call for call in calls if "--resume" in call["command"]]
        self.assertEqual(len(resume_calls), 2)
        workspaces = [call["command"][call["command"].index("--workspace") + 1] for call in resume_calls]
        self.assertEqual(workspaces[0], workspaces[1])
        self.assertIn("agentsassemble-cursor-resident-workspace-", Path(workspaces[0]).name)
        for call in resume_calls:
            command = call["command"]
            self.assertIn("--print", command)
            self.assertIn("--mode", command)
            self.assertIn("ask", command)
            self.assertIn("--sandbox", command)
            self.assertIn("enabled", command)
            self.assertIn("--trust", command)
            self.assertNotIn("/tmp/foreign", command)
            self.assertNotIn("smuggled-id", command)
            self.assertNotIn("SECRET-CODE", " ".join(command))
        self.assertFalse(workspace.exists())

    def test_runner_uses_configured_safe_session_id_without_create_chat(self):
        calls = []

        def command_runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="OK\n", stderr="")

        runner = CursorResidentCommandRunner(
            config(session_id="existing-chat-123"),
            command_runner=command_runner,
            cwd=Path.cwd(),
        )
        try:
            self.assertEqual(runner([], "hello", timeout_seconds=45), "OK")
        finally:
            runner.close()

        self.assertEqual(len(calls), 1)
        self.assertIn("--resume", calls[0])
        self.assertIn("existing-chat-123", calls[0])

    def test_runner_rejects_invalid_chat_id_without_leaking_output(self):
        def command_runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="unsafe id with spaces SECRET-CODE", stderr="SECRET-CODE")

        runner = CursorResidentCommandRunner(config(), command_runner=command_runner, cwd=Path.cwd())
        try:
            with self.assertRaisesRegex(ValueError, "safe Cursor chat id") as caught:
                runner([], "SECRET-CODE", timeout_seconds=45)
        finally:
            runner.close()
        self.assertEqual(cursor_error_category(caught.exception), CURSOR_INVALID_CHAT_ID)
        self.assertNotIn("SECRET-CODE", str(caught.exception))

    def test_runner_rejects_empty_reply(self):
        def command_runner(command, **kwargs):
            if command == ["cursor-agent", "create-chat"]:
                return subprocess.CompletedProcess(command, 0, stdout="cursor-chat-1\n", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="   \n", stderr="")

        runner = CursorResidentCommandRunner(config(), command_runner=command_runner, cwd=Path.cwd())
        try:
            with self.assertRaisesRegex(ValueError, "empty reply") as caught:
                runner([], "prompt", timeout_seconds=45)
        finally:
            runner.close()
        self.assertEqual(cursor_error_category(caught.exception), CURSOR_EMPTY_TEXT)

    def test_runner_treats_nonzero_and_timeout_as_safe_errors(self):
        def failed_runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 2, stdout="", stderr="token=SECRET")

        failed = CursorResidentCommandRunner(config(session_id="cursor-chat-1"), command_runner=failed_runner, cwd=Path.cwd())
        try:
            with self.assertRaisesRegex(RuntimeError, "return code 2") as failed_error:
                failed([], "prompt", timeout_seconds=45)
        finally:
            failed.close()
        self.assertEqual(cursor_error_category(failed_error.exception), CURSOR_SUBPROCESS_NONZERO)

        def timeout_runner(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        timed_out = CursorResidentCommandRunner(config(session_id="cursor-chat-1"), command_runner=timeout_runner, cwd=Path.cwd())
        try:
            with self.assertRaisesRegex(RuntimeError, "timed out after 45 seconds") as timeout_error:
                timed_out([], "prompt", timeout_seconds=45)
        finally:
            timed_out.close()
        self.assertEqual(cursor_error_category(timeout_error.exception), CURSOR_SUBPROCESS_TIMEOUT)

    def test_provider_checks_defaults_and_safe_chat_ids_are_narrow(self):
        self.assertEqual(default_cursor_resident_command("cursor_live_session", "live_session", []), ["cursor-agent"])
        self.assertEqual(default_cursor_resident_command("local_cli", "live_session", []), [])
        self.assertEqual(
            cursor_provider_connection_check("cursor_live_session", "live_session"),
            {
                "id": "provider_connection_kind",
                "status": "ok",
                "message": "cursor_live_session uses live_session.",
            },
        )
        self.assertEqual(cursor_provider_connection_check("local_cli", "live_session"), None)
        self.assertEqual(cursor_provider_connection_check("cursor", "self_service"), None)
        self.assertEqual(cursor_provider_connection_check("cursor", "remote_bridge"), None)
        self.assertEqual(cursor_provider_connection_check("cursor", "terminal_session")["status"], "failed")
        self.assertIn("cursor_live_session", cursor_provider_connection_check("cursor", "live_session")["message"])
        self.assertEqual(cursor_command_check(["cursor-agent"])["status"], "ok")
        self.assertEqual(cursor_command_check(["cursor-agent", "--print"])["status"], "failed")
        self.assertEqual(cursor_command_check(["cursor"])["status"], "failed")
        for unsafe in ["unsafe id", "../chat", "chat/1", "chat`1", "chat$1", "chat;1", "chat|1"]:
            with self.subTest(unsafe=unsafe):
                self.assertEqual(clean_cursor_chat_id(unsafe), "")
        self.assertEqual(clean_cursor_chat_id("019e3038-39cc-76a2-a746-5ba8c0f3b408"), "019e3038-39cc-76a2-a746-5ba8c0f3b408")


if __name__ == "__main__":
    unittest.main()
