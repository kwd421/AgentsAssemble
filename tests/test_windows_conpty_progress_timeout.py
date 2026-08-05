from __future__ import annotations

import queue
import sys
import time
import unittest

from agentsassemble.providers.live_cli_transcripts import LiveCliMessageSnapshot
from agentsassemble.providers.windows_conpty import WindowsConPtyRuntime


class _TranscriptSource:
    strict = True

    def __init__(self) -> None:
        self._reported: set[str] = set()
        self._activities: list[dict[str, object]] = []

    def prepare_start(self) -> None:
        return

    def begin_turn(self, expected_input: str = "") -> None:
        del expected_input

    def poll(self, terminal_output: bytes, *, quiet: bool = False) -> LiveCliMessageSnapshot:
        del quiet
        text = terminal_output.decode("utf-8", errors="replace")
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
        return {"message_source": "fake_strict", "message_source_strict": True}


class _FakeConPtyProcess:
    pid = 4321

    def __init__(self, chunks: list[tuple[float, str]]) -> None:
        self._chunks: queue.Queue[tuple[float, str]] = queue.Queue()
        for item in chunks:
            self._chunks.put(item)
        self.closed = False

    def write(self, _value: str) -> None:
        return

    def read(self, _size: int) -> str:
        try:
            delay, chunk = self._chunks.get(timeout=0.05)
        except queue.Empty:
            time.sleep(0.01)
            return ""
        time.sleep(delay)
        return chunk

    def isalive(self) -> bool:
        return not self.closed

    def terminate(self) -> None:
        self.closed = True


class WindowsConPtyProgressTimeoutTests(unittest.TestCase):
    def _runtime(self, chunks: list[tuple[float, str]]) -> WindowsConPtyRuntime:
        process = _FakeConPtyProcess(chunks)
        return WindowsConPtyRuntime(
            "windows-agent",
            [sys.executable],
            idle_quiet_seconds=0.01,
            message_source=_TranscriptSource(),
            process_factory=lambda *_args, **_kwargs: process,
        )

    def test_structured_progress_extends_the_conpty_inactivity_window(self):
        runtime = self._runtime(
            [
                (0.12, "working:one\n"),
                (0.12, "working:two\n"),
                (0.12, "answer:done\n"),
            ]
        )
        try:
            runtime.send("work")
            result = runtime.read_output(timeout_seconds=0.2)
        finally:
            runtime.stop()

        self.assertEqual(result["content"], "answer:done")

    def test_raw_conpty_spinner_bytes_do_not_extend_the_inactivity_window(self):
        runtime = self._runtime([(0.04, "spinner\n")] * 20)
        started_at = time.monotonic()
        try:
            runtime.send("work")
            with self.assertRaises(TimeoutError):
                runtime.read_output(timeout_seconds=0.15)
        finally:
            runtime.stop()

        self.assertLess(time.monotonic() - started_at, 0.45)


if __name__ == "__main__":
    unittest.main()
