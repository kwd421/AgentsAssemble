import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.grok_acp_runtime import GrokAcpRuntime


ROOT = Path(__file__).resolve().parents[1]
FAKE_ACP = ROOT / "tests" / "fixtures" / "fake_grok_acp.py"


class GrokAcpRuntimeTests(unittest.TestCase):
    def make_runtime(self, root: Path) -> GrokAcpRuntime:
        return GrokAcpRuntime(
            "grok",
            [sys.executable, "-u", str(FAKE_ACP)],
            cwd=ROOT,
            state_dir=root / "grok-home",
            auth_path=root / "auth.json",
            startup_timeout_seconds=5,
        )

    def test_persistent_structured_session_streams_only_agent_message_chunks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            try:
                first_health = runtime.start()
                first_pid = first_health["pid"]
                first_deltas: list[str] = []
                runtime.send("first turn")
                first = runtime.read_output(timeout_seconds=5, on_delta=first_deltas.append)
                runtime.send("second turn")
                second = runtime.read_output(timeout_seconds=5)
                second_health = runtime.health()
            finally:
                runtime.stop()

        self.assertEqual(first["content"], "remembered first turn")
        self.assertEqual("".join(first_deltas), first["content"])
        self.assertEqual(second["content"], "remembered second turn")
        self.assertEqual(first_pid, second_health["pid"])
        self.assertFalse(first_health["pty"])
        self.assertEqual(first_health["transport"], "acp_stdio")
        self.assertEqual(first["metadata"]["message_source"], "grok_acp")
        self.assertGreaterEqual(second_health["stderr_line_count"], 3000)
        self.assertGreater(second_health["stderr_byte_count"], 0)
        self.assertTrue(second_health["stderr_tail_truncated"])
        self.assertFalse(runtime.health()["running"])
        with self.assertRaises(OSError):
            os.kill(int(first_pid), 0)

    def test_permission_request_is_denied_without_hanging_turn(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            try:
                runtime.send("permission")
                output = runtime.read_output(timeout_seconds=5)
                health = runtime.health()
            finally:
                runtime.stop()

        self.assertEqual(output["content"], "permission denied safely")
        self.assertEqual(health["permission_request_count"], 1)
        self.assertEqual(health["permission_denied_count"], 1)

    def test_always_approve_session_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            try:
                runtime.send("unsafe-yolo")
                with self.assertRaisesRegex(RuntimeError, "always-approve mode is active"):
                    runtime.read_output(timeout_seconds=5)
            finally:
                runtime.stop()

    def test_always_approve_notification_queued_at_start_is_not_discarded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = GrokAcpRuntime(
                "grok",
                [sys.executable, "-u", str(FAKE_ACP)],
                cwd=ROOT,
                state_dir=root / "grok-home",
                auth_path=root / "auth.json",
                env={"FAKE_GROK_ACP_YOLO_ON_START": "1"},
                startup_timeout_seconds=5,
            )
            try:
                runtime.start()
                with self.assertRaisesRegex(RuntimeError, "always-approve mode is active"):
                    runtime.send("must not be delivered")
            finally:
                runtime.stop()

    def test_empty_completed_turn_fails_without_a_second_provider_prompt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            try:
                with patch.object(runtime, "_begin_request", wraps=runtime._begin_request) as begin_request:
                    runtime.send("empty-turn")
                    with self.assertRaisesRegex(RuntimeError, "without a room-visible assistant message") as raised:
                        runtime.read_output(timeout_seconds=5)
            finally:
                runtime.stop()

        prompt_calls = [call for call in begin_request.call_args_list if call.args[0] == "session/prompt"]
        self.assertEqual(len(prompt_calls), 1)
        self.assertEqual(getattr(raised.exception, "code", ""), "empty_provider_final")

    def test_provider_quota_error_is_classified_from_drained_stderr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            try:
                runtime.send("quota")
                with self.assertRaisesRegex(RuntimeError, "usage balance is exhausted"):
                    runtime.read_output(timeout_seconds=5)
                health = runtime.health()
            finally:
                runtime.stop()

        self.assertIn("usage balance is exhausted", health["last_error"])
        self.assertGreater(health["stderr_byte_count"], 64_000)

    def test_stderr_tail_storage_is_bounded_by_line_and_character_limits(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            for index in range(150):
                line = f"WARN-{index}-" + ("x" * 1000)
                runtime._record_stderr_line(line, byte_count=len(line.encode()))
            health = runtime.health()

        self.assertEqual(health["stderr_line_count"], 150)
        self.assertEqual(health["stderr_warning_count"], 150)
        self.assertTrue(health["stderr_tail_truncated"])
        self.assertLessEqual(len(health["stderr_tail"]), 16_000)
        self.assertIn("WARN-149", health["stderr_tail"])

    def test_structured_output_queue_loss_fails_instead_of_returning_truncated_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = GrokAcpRuntime(
                "grok",
                [sys.executable, "-u", str(FAKE_ACP)],
                cwd=ROOT,
                state_dir=root / "grok-home",
                auth_path=root / "auth.json",
                notification_queue_size=2,
                startup_timeout_seconds=5,
            )
            try:
                runtime.send("overflow")
                time.sleep(0.05)
                with self.assertRaisesRegex(RuntimeError, "output backpressure dropped"):
                    runtime.read_output(timeout_seconds=5)
                health = runtime.health()
            finally:
                runtime.stop()

        self.assertGreater(health["notification_drop_count"], 0)

    def test_same_runtime_restarts_after_process_exit_without_stale_eof(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self.make_runtime(Path(temp_dir))
            try:
                first_pid = runtime.start()["pid"]
                runtime.send("exit-mid-turn")
                with self.assertRaisesRegex(RuntimeError, "exited before turn completion"):
                    runtime.read_output(timeout_seconds=5)

                runtime.send("after-restart")
                restarted = runtime.read_output(timeout_seconds=5)
                second_health = runtime.health()
            finally:
                runtime.stop()

        self.assertNotEqual(first_pid, second_health["pid"])
        self.assertTrue(second_health["provider_session_reused"])
        self.assertEqual(restarted["content"], "remembered after-restart")

    def test_restart_loads_the_same_provider_session_and_preserves_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_runtime = self.make_runtime(root)
            try:
                first_health = first_runtime.start()
                first_runtime.send("restart-marker-413")
                first_runtime.read_output(timeout_seconds=5)
            finally:
                first_runtime.stop()

            second_runtime = self.make_runtime(root)
            try:
                second_health = second_runtime.start()
                second_runtime.send("recall-after-restart")
                recalled = second_runtime.read_output(timeout_seconds=5)
                state_mode = (root / "grok-home" / "agentsassemble-session.json").stat().st_mode & 0o777
            finally:
                second_runtime.stop()

        self.assertFalse(first_health["provider_session_reused"])
        self.assertTrue(second_health["provider_session_load_supported"])
        self.assertTrue(second_health["provider_session_reused"])
        self.assertFalse(second_health["provider_session_resume_failed"])
        self.assertEqual(recalled["content"], "recalled restart-marker-413")
        self.assertEqual(state_mode, 0o600)

    def test_failed_session_load_starts_fresh_and_reports_recovery_loss(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_runtime = self.make_runtime(root)
            try:
                first_runtime.start()
            finally:
                first_runtime.stop()

            second_runtime = GrokAcpRuntime(
                "grok",
                [sys.executable, "-u", str(FAKE_ACP)],
                cwd=ROOT,
                state_dir=root / "grok-home",
                auth_path=root / "auth.json",
                env={"FAKE_GROK_ACP_LOAD_FAIL": "1"},
                startup_timeout_seconds=5,
            )
            try:
                health = second_runtime.start()
            finally:
                second_runtime.stop()

        self.assertFalse(health["provider_session_reused"])
        self.assertTrue(health["provider_session_resume_failed"])
        self.assertIn("stored session is unavailable", health["provider_session_resume_error"])
        self.assertEqual(health["last_error"], "")


if __name__ == "__main__":
    unittest.main()
