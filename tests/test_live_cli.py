import os
import sys
import time
import unittest
from unittest.mock import patch

from agentsassemble.live_cli import (
    ApiRuntime,
    LiveCliRuntime,
    live_cli_supported,
)


def _fake_cli_script(*, delay_seconds: float = 0.0) -> str:
    return "\n".join(
        [
            "import sys, time",
            "count = 0",
            "memory = []",
            "for line in sys.stdin:",
            "    text = line.strip()",
            "    if not text:",
            "        continue",
            "    count += 1",
            "    memory.append(text)",
            f"    time.sleep({delay_seconds!r})",
            "    print(f'reply {count}: {text} | remembered {len(memory)}', flush=True)",
        ]
    )


class LiveCliRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_live_cli_runtime_keeps_one_process_state_across_deliveries(self):
        runtime = LiveCliRuntime("alpha", [sys.executable, "-u", "-c", _fake_cli_script()], idle_quiet_seconds=0.05)
        try:
            runtime.start()
            first_pid = runtime.health()["pid"]
            first_event = {"event_id": "evt-1", "actor_id": "human", "actor_type": "user", "kind": "user_message", "content": "first"}
            runtime.deliver([first_event])
            first = runtime.read_output(timeout_seconds=2)
            second_event = {"event_id": "evt-2", "actor_id": "human", "actor_type": "user", "kind": "user_message", "content": "second"}
            runtime.deliver([second_event])
            second = runtime.read_output(timeout_seconds=2)
            second_pid = runtime.health()["pid"]
        finally:
            runtime.stop()

        self.assertEqual(first_pid, second_pid)
        self.assertIn("reply 1: #general human: first | remembered 1", first["content"])
        self.assertIn("reply 2: #general human: second | remembered 2", second["content"])
        self.assertEqual(runtime.last_seen_event_id, "evt-2")

    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_live_cli_runtime_can_send_bracketed_paste_to_tui(self):
        runtime = LiveCliRuntime(
            "alpha",
            [sys.executable, "-u", "-c", _fake_cli_script()],
            idle_quiet_seconds=0.05,
            input_mode="bracketed_paste",
            submit_newline="\r",
            terminal_columns=100,
            terminal_rows=30,
        )
        try:
            runtime.start()
            runtime.deliver(
                [
                    {
                        "event_id": "evt-1",
                        "actor_id": "human",
                        "actor_type": "user",
                        "kind": "user_message",
                        "content": "first",
                    }
                ]
            )
            first = runtime.read_output(timeout_seconds=2)
            health = runtime.health()
        finally:
            runtime.stop()

        self.assertIn("reply 1:", first["content"])
        self.assertIn("#general human: first", first["content"])
        self.assertEqual(health["input_mode"], "bracketed_paste")
        self.assertEqual(health["terminal_columns"], 100)
        self.assertEqual(health["terminal_rows"], 30)

    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_live_cli_runtime_drains_startup_banner_before_first_input(self):
        script = "\n".join(
            [
                "import sys, time",
                "print('startup banner', flush=True)",
                "time.sleep(0.05)",
                "print('ready prompt', flush=True)",
                "for line in sys.stdin:",
                "    text = line.strip()",
                "    if text:",
                "        print('reply: ' + text, flush=True)",
            ]
        )
        runtime = LiveCliRuntime(
            "alpha",
            [sys.executable, "-u", "-c", script],
            idle_quiet_seconds=0.05,
            startup_quiet_seconds=0.05,
            startup_timeout_seconds=1.0,
        )
        try:
            runtime.start()
            runtime.deliver(
                [
                    {
                        "event_id": "evt-1",
                        "actor_id": "human",
                        "actor_type": "user",
                        "kind": "user_message",
                        "content": "first",
                    }
                ]
            )
            output = runtime.read_output(timeout_seconds=2)
        finally:
            runtime.stop()

        self.assertIn("reply: #general human: first", output["content"])
        self.assertNotIn("startup banner", output["content"])

    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_live_cli_runtime_does_not_inherit_parent_agent_session_identity(self):
        script = "\n".join(
            [
                "import os, sys",
                "for line in sys.stdin:",
                "    print('|'.join(str(os.environ.get(key)) for key in "
                "['CODEX_THREAD_ID', 'CODEX_CI', 'CLAUDECODE', 'CLAUDE_CODE_SESSION_ID']), flush=True)",
            ]
        )
        with patch.dict(
            os.environ,
            {
                "CODEX_THREAD_ID": "parent-codex-thread",
                "CODEX_CI": "1",
                "CLAUDECODE": "1",
                "CLAUDE_CODE_SESSION_ID": "parent-claude-session",
            },
        ):
            runtime = LiveCliRuntime(
                "fake",
                [sys.executable, "-u", "-c", script],
                idle_quiet_seconds=0.05,
            )
            try:
                runtime.send("check")
                output = runtime.read_output(timeout_seconds=2)
                health = runtime.health()
            finally:
                runtime.stop()

        self.assertIn("None|None|None|None", output["content"])
        self.assertIn("CODEX_THREAD_ID", health["parent_agent_session_env_removed"])
        self.assertIn("CLAUDECODE", health["parent_agent_session_env_removed"])

    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_live_cli_restart_replaces_process_after_stop(self):
        runtime = LiveCliRuntime("alpha", [sys.executable, "-u", "-c", _fake_cli_script()], idle_quiet_seconds=0.05)
        try:
            runtime.start()
            first_pid = runtime.health()["pid"]
            runtime.restart()
            second_pid = runtime.health()["pid"]
        finally:
            runtime.stop()

        self.assertNotEqual(first_pid, second_pid)
        self.assertTrue(runtime.health()["stopped"])

    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_stop_does_not_wait_for_blocked_read_timeout(self):
        import threading

        script = "\n".join(
            [
                "import sys, time",
                "for line in sys.stdin:",
                "    time.sleep(5)",
            ]
        )
        runtime = LiveCliRuntime("alpha", [sys.executable, "-u", "-c", script], idle_quiet_seconds=0.05)
        runtime.start()
        runtime.deliver([{"event_id": "evt-1", "actor_id": "human", "actor_type": "user", "kind": "user_message", "content": "block"}])
        errors = []

        def read_forever():
            try:
                runtime.read_output(timeout_seconds=5)
            except Exception as error:
                errors.append(error)

        reader = threading.Thread(target=read_forever)
        reader.start()
        time.sleep(0.1)

        started = time.monotonic()
        runtime.stop(timeout_seconds=0.1)
        elapsed = time.monotonic() - started
        reader.join(timeout=1)

        self.assertLess(elapsed, 1.0)
        self.assertFalse(reader.is_alive())
        self.assertTrue(errors)


class ApiRuntimeStubTests(unittest.TestCase):
    def test_api_runtime_stub_uses_agent_runtime_shape_without_complete_prompt_method(self):
        runtime = ApiRuntime("api-later")

        self.assertFalse(hasattr(runtime, "complete"))
        self.assertEqual(runtime.health()["status"], "unsupported")
        with self.assertRaisesRegex(RuntimeError, "later AgentRuntime implementation"):
            runtime.read_output(timeout_seconds=1)
