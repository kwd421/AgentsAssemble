import sys
import time
import unittest

from agentsassemble.providers.live_cli import LiveCliRuntime, live_cli_supported
from agentsassemble.providers.live_cli_transcripts import LiveCliMessageSnapshot


class CompletedAnswerMessageSource:
    strict = True

    def __init__(self) -> None:
        self._reported: set[str] = set()
        self._activities: list[dict[str, object]] = []

    def prepare_start(self) -> None:
        return

    def begin_turn(self, expected_input: str = "") -> None:
        del expected_input

    def poll(
        self,
        terminal_output: bytes,
        *,
        quiet: bool = False,
    ) -> LiveCliMessageSnapshot:
        del quiet
        text = terminal_output.decode("utf-8", errors="replace").replace("\r", "")
        for marker in ("working:one", "working:two"):
            if marker in text and marker not in self._reported:
                self._reported.add(marker)
                self._activities.append({"kind": "progress", "summary": marker})
        if "answer:done" not in text:
            return LiveCliMessageSnapshot()
        return LiveCliMessageSnapshot(
            content="answer:done",
            complete=True,
            source="fake-transcript",
            source_kind="fake_strict",
        )

    def drain_activities(self) -> list[dict[str, object]]:
        activities = self._activities
        self._activities = []
        return activities

    def describe(self) -> dict[str, object]:
        return {
            "message_source": "fake_strict",
            "message_source_strict": True,
        }


class LiveCliProgressTimeoutTests(unittest.TestCase):
    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_strict_transcript_turn_survives_bounded_terminal_noise(self):
        script = "\n".join(
            [
                "import sys",
                "for line in sys.stdin:",
                "    if line.strip():",
                "        print('x' * 5000, flush=True)",
                "        print('answer:done', flush=True)",
            ]
        )
        runtime = LiveCliRuntime(
            "noisy-transcript",
            [sys.executable, "-u", "-c", script],
            idle_quiet_seconds=0.02,
            max_output_bytes=1024,
            message_source=CompletedAnswerMessageSource(),
        )
        try:
            runtime.send("work")
            output = runtime.read_output(timeout_seconds=1)
            health = runtime.health()
        finally:
            runtime.stop()

        self.assertEqual(output["content"], "answer:done")
        self.assertGreater(health["last_turn_truncated_terminal_bytes"], 0)

    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_terminal_canonical_output_still_fails_when_capture_is_oversized(self):
        script = "\n".join(
            [
                "import sys",
                "for line in sys.stdin:",
                "    if line.strip():",
                "        print('x' * 5000, flush=True)",
            ]
        )
        runtime = LiveCliRuntime(
            "noisy-terminal",
            [sys.executable, "-u", "-c", script],
            idle_quiet_seconds=0.02,
            max_output_bytes=1024,
        )
        try:
            runtime.send("work")
            with self.assertRaises(ValueError):
                runtime.read_output(timeout_seconds=1)
        finally:
            runtime.stop()

    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_active_turn_progress_extends_timeout_window(self):
        script = "\n".join(
            [
                "import sys, time",
                "for line in sys.stdin:",
                "    if not line.strip():",
                "        continue",
                "    time.sleep(0.3)",
                "    print('working:one', flush=True)",
                "    time.sleep(0.3)",
                "    print('working:two', flush=True)",
                "    time.sleep(0.3)",
                "    print('answer:done', flush=True)",
            ]
        )
        runtime = LiveCliRuntime(
            "alpha",
            [sys.executable, "-u", "-c", script],
            idle_quiet_seconds=0.02,
            message_source=CompletedAnswerMessageSource(),
        )
        try:
            runtime.send("work")
            output = runtime.read_output(timeout_seconds=0.5)
        finally:
            runtime.stop()

        self.assertEqual(output["content"], "answer:done")

    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_terminal_spinner_bytes_do_not_extend_the_progress_timeout(self):
        script = "\n".join(
            [
                "import sys, time",
                "for line in sys.stdin:",
                "    if not line.strip():",
                "        continue",
                "    for frame in range(20):",
                "        print('spinner', frame, flush=True)",
                "        time.sleep(0.05)",
            ]
        )
        runtime = LiveCliRuntime(
            "spinner-only",
            [sys.executable, "-u", "-c", script],
            idle_quiet_seconds=0.02,
            message_source=CompletedAnswerMessageSource(),
        )
        started_at = time.monotonic()
        try:
            runtime.send("work")
            with self.assertRaises(TimeoutError):
                runtime.read_output(timeout_seconds=0.15)
        finally:
            runtime.stop()

        self.assertLess(time.monotonic() - started_at, 0.4)


if __name__ == "__main__":
    unittest.main()
