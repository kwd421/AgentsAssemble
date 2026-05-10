import json
import unittest
from unittest.mock import Mock

from agentsassemble.bridges.claude_code_bridge import run_bridge_request


class ClaudeCodeBridgeTests(unittest.TestCase):
    def test_bridge_runs_claude_print_with_prompt(self):
        runner = Mock()
        runner.return_value.stdout = '{"content":"참가 의견","position":"유지","stance_status":"held","change_conditions":[],"confidence":"medium"}'
        runner.return_value.stderr = ""
        runner.return_value.returncode = 0
        payload = {
            "step": "round",
            "role": {"id": "fanboard_skeptic", "display_name": "만갤러"},
            "prompt": "Return only JSON",
        }

        response = run_bridge_request(payload, command="claude", runner=runner)

        self.assertEqual(json.loads(response["text"])["content"], "참가 의견")
        self.assertEqual(response["metadata"]["command"], "claude -p")
        self.assertEqual(response["metadata"]["role_id"], "fanboard_skeptic")
        self.assertEqual(runner.call_args.args[0], ["claude", "-p"])
        self.assertEqual(runner.call_args.kwargs["input"], "Return only JSON")
        self.assertEqual(runner.call_args.kwargs["timeout"], 300)

    def test_bridge_reports_failed_claude_command_without_hiding_error(self):
        runner = Mock()
        runner.return_value.stdout = ""
        runner.return_value.stderr = "not logged in"
        runner.return_value.returncode = 1

        response = run_bridge_request({"prompt": "hello"}, runner=runner)

        self.assertIn("Claude Code bridge failed", response["text"])
        self.assertEqual(response["metadata"]["returncode"], 1)
        self.assertIn("not logged in", response["metadata"]["stderr"])


if __name__ == "__main__":
    unittest.main()
