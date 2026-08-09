import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.providers.opencode import OpenCodeRuntime


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def close(self):
        return None


class OpenCodeSessionRecoveryTests(unittest.TestCase):
    def test_probe_failure_does_not_replace_a_stored_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            session_path = state_dir / "session.json"
            session_path.write_text(
                json.dumps(
                    {
                        "session_id": "existing-session",
                        "model": "opencode-go/glm-5.2",
                        "variant": "",
                    }
                ),
                encoding="utf-8",
            )

            def opener(request, timeout):
                del timeout
                if request.get_method() == "GET":
                    raise TimeoutError("OpenCode session probe timed out")
                if request.get_method() == "POST" and "/session?" in request.full_url:
                    return _Response({"id": "replacement-session"})
                raise AssertionError(request.full_url)

            runtime = OpenCodeRuntime(
                "opencode",
                endpoint="http://127.0.0.1:1",
                workspace="/tmp",
                state_dir=state_dir,
                opener=opener,
            )

            with self.assertRaisesRegex(TimeoutError, "probe timed out"):
                runtime.start()

            persisted = json.loads(session_path.read_text(encoding="utf-8"))

        self.assertEqual(persisted["session_id"], "existing-session")
        self.assertFalse(runtime.health()["running"])

    def test_invalid_stored_session_state_fails_instead_of_starting_fresh(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            session_path = state_dir / "session.json"
            session_path.write_text("not-json", encoding="utf-8")

            def opener(request, timeout):
                del timeout
                if request.get_method() == "POST" and "/session?" in request.full_url:
                    return _Response({"id": "replacement-session"})
                raise AssertionError(request.full_url)

            runtime = OpenCodeRuntime(
                "opencode",
                endpoint="http://127.0.0.1:1",
                workspace="/tmp",
                state_dir=state_dir,
                opener=opener,
            )

            with self.assertRaisesRegex(RuntimeError, "stored OpenCode session state"):
                runtime.start()

            persisted = session_path.read_text(encoding="utf-8")

        self.assertEqual(persisted, "not-json")
        self.assertFalse(runtime.health()["running"])


if __name__ == "__main__":
    unittest.main()
