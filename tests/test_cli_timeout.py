import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.cli import build_parser, main


class CliTimeoutTests(unittest.TestCase):
    def test_codex_timeout_can_be_disabled(self):
        args = build_parser().parse_args(["demo", "--adapter", "codex", "--codex-timeout", "none"])

        self.assertIsNone(args.codex_timeout)

    def test_demo_accepts_codex_live_adapter(self):
        args = build_parser().parse_args(["demo", "--adapter", "codex-live"])

        self.assertEqual(args.adapter, "codex-live")

    def test_demo_accepts_council_config_path(self):
        args = build_parser().parse_args(["demo", "--council-config", "configs/silly-fake-expert.json"])

        self.assertEqual(args.council_config, "configs/silly-fake-expert.json")

    def test_demo_accepts_meeting_mode_and_moderator_options(self):
        args = build_parser().parse_args(["demo", "--meeting-mode", "free-chat", "--moderator", "off"])

        self.assertEqual(args.meeting_mode, "free-chat")
        self.assertEqual(args.moderator, "off")

    def test_demo_passes_meeting_mode_and_moderator_to_runner(self):
        with patch("agentsassemble.cli.run_demo_meeting") as run_demo:
            exit_code = main(["demo", "--meeting-mode", "free-chat", "--moderator", "off", "--output-root", "out"])

        self.assertEqual(exit_code, 0)
        run_demo.assert_called_once()
        kwargs = run_demo.call_args.kwargs
        self.assertEqual(kwargs["meeting_mode"], "free_chat")
        self.assertFalse(kwargs["moderator_enabled"])
        self.assertEqual(kwargs["output_root"], Path("out"))

    def test_demo_accepts_follow_up_metadata(self):
        args = build_parser().parse_args(
            [
                "demo",
                "--follow-up-of",
                "meeting-1",
                "--follow-up-from",
                ".agentsassemble/meetings/meeting-1",
                "--follow-up-note",
                "reopen unresolved caveat",
            ]
        )

        self.assertEqual(args.follow_up_of, "meeting-1")
        self.assertEqual(args.follow_up_from, ".agentsassemble/meetings/meeting-1")
        self.assertEqual(args.follow_up_note, "reopen unresolved caveat")

    def test_deep_codex_defaults_to_no_timeout(self):
        args = build_parser().parse_args(["demo", "--adapter", "codex", "--research-depth", "deep"])

        self.assertIsNone(args.codex_timeout)

    def test_claude_bridge_parses_bridge_command_without_overwriting_subcommand(self):
        args = build_parser().parse_args(["claude-bridge", "--token", "bridge-token", "--command", "claude"])

        self.assertEqual(args.command, "claude-bridge")
        self.assertEqual(args.bridge_command, "claude")


if __name__ == "__main__":
    unittest.main()
