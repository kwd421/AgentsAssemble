import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StaticMeetingViewsRuntimeTests(unittest.TestCase):
    def test_live_transcript_refresh_preserves_existing_dom_rows(self):
        result = subprocess.run(
            ["node", "--test", "tests/static_meeting_views_runtime_smoke.mjs"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
