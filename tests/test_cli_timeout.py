import unittest

from agentsassemble.cli import build_parser


class CliTimeoutTests(unittest.TestCase):
    def test_codex_timeout_can_be_disabled(self):
        args = build_parser().parse_args(["demo", "--adapter", "codex", "--codex-timeout", "none"])

        self.assertIsNone(args.codex_timeout)

    def test_demo_accepts_council_config_path(self):
        args = build_parser().parse_args(["demo", "--council-config", "configs/silly-fake-expert.json"])

        self.assertEqual(args.council_config, "configs/silly-fake-expert.json")

    def test_deep_codex_defaults_to_no_timeout(self):
        args = build_parser().parse_args(["demo", "--adapter", "codex", "--research-depth", "deep"])

        self.assertIsNone(args.codex_timeout)

    def test_claude_bridge_parses_bridge_command_without_overwriting_subcommand(self):
        args = build_parser().parse_args(["claude-bridge", "--command", "claude"])

        self.assertEqual(args.command, "claude-bridge")
        self.assertEqual(args.bridge_command, "claude")


if __name__ == "__main__":
    unittest.main()
