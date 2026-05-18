import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StaticLobbyRuntimeTests(unittest.TestCase):
    def test_live_agent_readiness_button_posts_runtime_request_body(self):
        result = subprocess.run(
            ["node", "--test", "tests/static_lobby_readiness_smoke.mjs"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
