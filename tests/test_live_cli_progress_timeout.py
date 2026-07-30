import sys
import unittest

from agentsassemble.providers.live_cli import LiveCliRuntime, live_cli_supported
from agentsassemble.providers.live_cli_transcripts import LiveCliMessageSnapshot


class CompletedAnswerMessageSource:
    strict = True

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
        if "answer:done" not in text:
            return LiveCliMessageSnapshot()
        return LiveCliMessageSnapshot(
            content="answer:done",
            complete=True,
            source="fake-transcript",
            source_kind="fake_strict",
        )

    def describe(self) -> dict[str, object]:
        return {
            "message_source": "fake_strict",
            "message_source_strict": True,
        }


class LiveCliProgressTimeoutTests(unittest.TestCase):
    @unittest.skipUnless(live_cli_supported(), "requires POSIX PTY support")
    def test_active_turn_progress_extends_timeout_window(self):
        script = "\n".join(
            [
                "import sys, time",
                "for line in sys.stdin:",
                "    if not line.strip():",
                "        continue",
                "    time.sleep(0.12)",
                "    print('working:one', flush=True)",
                "    time.sleep(0.12)",
                "    print('working:two', flush=True)",
                "    time.sleep(0.12)",
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
            output = runtime.read_output(timeout_seconds=0.2)
        finally:
            runtime.stop()

        self.assertEqual(output["content"], "answer:done")


if __name__ == "__main__":
    unittest.main()
