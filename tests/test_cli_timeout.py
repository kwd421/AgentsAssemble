import unittest

from agentsassemble.cli import build_parser


class CliTimeoutTests(unittest.TestCase):
    def test_codex_timeout_can_be_disabled(self):
        args = build_parser().parse_args(["demo", "--adapter", "codex", "--codex-timeout", "none"])

        self.assertIsNone(args.codex_timeout)

    def test_deep_codex_defaults_to_no_timeout(self):
        args = build_parser().parse_args(["demo", "--adapter", "codex", "--research-depth", "deep"])

        self.assertIsNone(args.codex_timeout)


if __name__ == "__main__":
    unittest.main()
