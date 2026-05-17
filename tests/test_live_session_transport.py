import json
import sys
import threading
import time
import unittest

from agentsassemble.live_session_transport import JsonlLiveSession


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
