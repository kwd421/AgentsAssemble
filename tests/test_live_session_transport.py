import json
import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from agentsassemble.live_session_transport import JsonlLiveSession


def _supports_process_groups():
    return hasattr(os, "killpg") and hasattr(os, "setsid") and hasattr(os, "getpgid")


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_pid_exit(pid: int, *, timeout_seconds: float = 5.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not _pid_exists(pid):
            return True
        time.sleep(0.01)
    return not _pid_exists(pid)


class JsonlLiveSessionTests(unittest.TestCase):
    def test_jsonl_session_keeps_one_process_state_across_prompts(self):
        script = "\n".join(
            [
                "import json, sys",
                "count = 0",
                "for line in sys.stdin:",
                "    payload = json.loads(line)",
                "    count += 1",
                "    print(json.dumps({'request_id': payload['request_id'], 'message': f\"state {count}: {payload['prompt'].splitlines()[0]}\"}), flush=True)",
            ]
        )
        session = JsonlLiveSession([sys.executable, "-u", "-c", script])
        try:
            first = session.ask("first prompt\ncontext", timeout_seconds=2)
            second = session.ask("second prompt\ncontext", timeout_seconds=2)
        finally:
            session.close()

        self.assertEqual(first, "state 1: first prompt")
        self.assertEqual(second, "state 2: second prompt")

    def test_jsonl_session_rejects_invalid_json_response(self):
        script = "print('not json', flush=True)"
        session = JsonlLiveSession([sys.executable, "-u", "-c", script])
        try:
            with self.assertRaisesRegex(ValueError, "invalid JSONL"):
                session.ask("prompt", timeout_seconds=2)
        finally:
            session.close()

    def test_jsonl_session_requires_message_field(self):
        script = "\n".join(
            [
                "import json, sys",
                "for line in sys.stdin:",
                "    payload = json.loads(line)",
                "    print(json.dumps({'request_id': payload['request_id']}), flush=True)",
            ]
        )
        session = JsonlLiveSession([sys.executable, "-u", "-c", script])
        try:
            with self.assertRaisesRegex(ValueError, "message"):
                session.ask("prompt", timeout_seconds=2)
        finally:
            session.close()

    def test_jsonl_session_timeout_closes_process(self):
        script = "\n".join(
            [
                "import sys, time",
                "for line in sys.stdin:",
                "    time.sleep(5)",
            ]
        )
        session = JsonlLiveSession([sys.executable, "-u", "-c", script])

        with self.assertRaisesRegex(TimeoutError, "timed out"):
            session.ask("prompt", timeout_seconds=0.1)

        self.assertIsNotNone(session.process.poll())

    @unittest.skipUnless(_supports_process_groups(), "requires POSIX process-group support")
    def test_jsonl_session_close_terminates_child_processes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            child_pid_path = Path(temp_dir) / "jsonl-child.pid"
            script = (
                "import pathlib, subprocess, sys, time; "
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8'); "
                "time.sleep(30)"
            )
            session = JsonlLiveSession([sys.executable, "-u", "-c", script, str(child_pid_path)])
            child_pid = None
            child_alive_after_close = None
            try:
                deadline = time.time() + 5
                while time.time() < deadline and not child_pid_path.exists():
                    time.sleep(0.01)
                self.assertTrue(child_pid_path.exists())
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))

                session.close(timeout_seconds=0.1)
                child_alive_after_close = not _wait_for_pid_exit(child_pid)
            finally:
                if child_pid is not None and _pid_exists(child_pid):
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                session.close()

            self.assertFalse(child_alive_after_close)

    @unittest.skipUnless(_supports_process_groups(), "requires POSIX process-group support")
    def test_jsonl_session_close_terminates_child_after_wrapper_exits_on_eof(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            child_pid_path = Path(temp_dir) / "jsonl-eof-child.pid"
            script = "\n".join(
                [
                    "import pathlib, subprocess, sys",
                    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])",
                    "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')",
                    "for _ in sys.stdin:",
                    "    pass",
                ]
            )
            session = JsonlLiveSession([sys.executable, "-u", "-c", script, str(child_pid_path)])
            child_pid = None
            child_alive_after_close = None
            try:
                deadline = time.time() + 5
                while time.time() < deadline and not child_pid_path.exists():
                    time.sleep(0.01)
                self.assertTrue(child_pid_path.exists())
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))

                session.close(timeout_seconds=0.5)
                child_alive_after_close = not _wait_for_pid_exit(child_pid)
            finally:
                if child_pid is not None and _pid_exists(child_pid):
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                session.close()

            self.assertEqual(session.process.poll(), 0)
            self.assertFalse(child_alive_after_close)

    @unittest.skipUnless(_supports_process_groups(), "requires POSIX process-group support")
    def test_jsonl_session_timeout_terminates_child_processes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            child_pid_path = Path(temp_dir) / "jsonl-timeout-child.pid"
            script = "\n".join(
                [
                    "import pathlib, subprocess, sys, time",
                    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])",
                    "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')",
                    "for line in sys.stdin:",
                    "    time.sleep(30)",
                ]
            )
            session = JsonlLiveSession([sys.executable, "-u", "-c", script, str(child_pid_path)])
            child_pid = None
            child_alive_after_timeout = None
            try:
                deadline = time.time() + 5
                while time.time() < deadline and not child_pid_path.exists():
                    time.sleep(0.01)
                self.assertTrue(child_pid_path.exists())
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))

                with self.assertRaisesRegex(TimeoutError, "timed out"):
                    session.ask("prompt", timeout_seconds=0.1)
                child_alive_after_timeout = not _wait_for_pid_exit(child_pid)
            finally:
                if child_pid is not None and _pid_exists(child_pid):
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                session.close()

            self.assertFalse(child_alive_after_timeout)

    def test_jsonl_session_close_interrupts_blocked_ask(self):
        script = "\n".join(
            [
                "import sys, time",
                "for line in sys.stdin:",
                "    time.sleep(30)",
            ]
        )
        session = JsonlLiveSession([sys.executable, "-u", "-c", script])
        errors = []

        def ask_session():
            try:
                session.ask("prompt", timeout_seconds=30)
            except Exception as error:
                errors.append(str(error))

        thread = threading.Thread(target=ask_session)
        thread.start()
        time.sleep(0.1)
        session.close(timeout_seconds=0.1)
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertTrue(errors)

    def test_jsonl_session_close_interrupts_blocked_stdin_write(self):
        script = "import time; time.sleep(30)"
        session = JsonlLiveSession([sys.executable, "-u", "-c", script])
        errors = []

        def ask_session():
            try:
                session.ask("x" * 5_000_000, timeout_seconds=30)
            except Exception as error:
                errors.append(str(error))

        thread = threading.Thread(target=ask_session)
        thread.start()
        time.sleep(0.1)
        started = time.monotonic()
        session.close(timeout_seconds=0.1)
        thread.join(timeout=1)
        elapsed = time.monotonic() - started

        self.assertFalse(thread.is_alive())
        self.assertLess(elapsed, 1.0)
        self.assertTrue(errors)

    def test_jsonl_session_timeout_covers_blocked_stdin_write(self):
        script = "import time; time.sleep(2)"
        session = JsonlLiveSession([sys.executable, "-u", "-c", script])

        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "timed out"):
            session.ask("x" * 5_000_000, timeout_seconds=0.1)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.0)
        self.assertIsNotNone(session.process.poll())

    def test_jsonl_session_reports_bounded_stderr_tail(self):
        script = "\n".join(
            [
                "import sys",
                "print('x' * 2000, file=sys.stderr, flush=True)",
                "sys.exit(7)",
            ]
        )
        session = JsonlLiveSession([sys.executable, "-u", "-c", script], stderr_tail_limit=120)
        try:
            with self.assertRaisesRegex(RuntimeError, "stderr tail:"):
                session.ask("prompt", timeout_seconds=2)
        finally:
            session.close()

        self.assertLessEqual(len(session.stderr_tail), 120)


if __name__ == "__main__":
    unittest.main()
