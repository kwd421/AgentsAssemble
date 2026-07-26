import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


HOOK_PATH = (
    Path(__file__).resolve().parents[1]
    / ".codex"
    / "hooks"
    / "block_provider_print.py"
)
SPEC = importlib.util.spec_from_file_location("block_provider_print", HOOK_PATH)
assert SPEC is not None and SPEC.loader is not None
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


class ProviderPrintHookTests(unittest.TestCase):
    def test_rejects_one_shot_provider_modes_through_common_shell_wrappers(self):
        commands = {
            "claude -p 'join the room'": "Claude print mode",
            "/opt/homebrew/bin/claude --print=json": "Claude print mode",
            "env GROK_HOME=/tmp/grok grok --prompt test": "Grok prompt mode",
            "command agy --print test": "Antigravity print mode",
            "codex -c model_reasoning_effort=low exec test": "Codex exec mode",
            "bash -lc \"claude -p test\"": "Claude print mode",
        }

        for command, expected in commands.items():
            with self.subTest(command=command):
                self.assertIn(expected, POLICY.provider_print_violation(command))

    def test_allows_canonical_provider_commands_and_text_searches(self):
        commands = (
            "claude --model claude-sonnet-4-6",
            "grok agent --model grok-4.5 stdio",
            "agy --model gemini-3.6-flash-low",
            "codex --version",
            "rg -n 'claude -p|agy --print' agentsassemble tests",
        )

        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(POLICY.provider_print_violation(command), "")

    def test_pre_tool_hook_returns_a_deny_decision_before_execution(self):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "claude -p test"},
        }

        completed = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0)
        output = json.loads(completed.stdout)
        decision = output["hookSpecificOutput"]
        self.assertEqual(decision["hookEventName"], "PreToolUse")
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("canonical interactive PTY", decision["permissionDecisionReason"])


if __name__ == "__main__":
    unittest.main()
