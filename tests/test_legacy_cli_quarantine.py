from __future__ import annotations

import argparse
import os
import unittest
from contextlib import redirect_stderr
from io import StringIO
from unittest.mock import Mock, patch

import agentsassemble.cli as cli
from agentsassemble.legacy.runtime_policy import (
    RETAINED_LEGACY_CLI_COMMANDS,
    UNSAFE_LEGACY_MUTATIONS_ENV,
    legacy_cli_command_quarantined,
)


class LegacyCliQuarantineTests(unittest.TestCase):
    def test_retained_legacy_commands_are_quarantined_by_default(self) -> None:
        for command in RETAINED_LEGACY_CLI_COMMANDS:
            with self.subTest(command=command):
                self.assertTrue(
                    legacy_cli_command_quarantined(command, environ={})
                )

    def test_current_commands_remain_available(self) -> None:
        for command in (
            "api-call",
            "frontend-info",
            "gui",
            "persona",
            "providers",
            "release-health",
            "rolling-restart",
            "room",
        ):
            with self.subTest(command=command):
                self.assertFalse(
                    legacy_cli_command_quarantined(command, environ={})
                )

    def test_only_exact_one_restores_retained_legacy_commands(self) -> None:
        for value in ("true", " 1", "1 ", "01"):
            with self.subTest(value=value):
                self.assertTrue(
                    legacy_cli_command_quarantined(
                        "demo",
                        environ={UNSAFE_LEGACY_MUTATIONS_ENV: value},
                    )
                )
        self.assertFalse(
            legacy_cli_command_quarantined(
                "demo",
                environ={UNSAFE_LEGACY_MUTATIONS_ENV: "1"},
            )
        )

    def test_main_blocks_legacy_command_before_handler_execution(self) -> None:
        parser = Mock()
        parser.parse_args.return_value = argparse.Namespace(command="demo")
        stderr = StringIO()

        with (
            patch.dict(
                os.environ,
                {UNSAFE_LEGACY_MUTATIONS_ENV: ""},
                clear=False,
            ),
            patch("agentsassemble.cli.build_parser", return_value=parser),
            patch("agentsassemble.cli.run_demo_command") as handler,
            redirect_stderr(stderr),
        ):
            result = cli.main([])

        self.assertEqual(result, 2)
        handler.assert_not_called()
        self.assertIn("Legacy CLI command 'demo' is disabled", stderr.getvalue())
        self.assertIn(UNSAFE_LEGACY_MUTATIONS_ENV, stderr.getvalue())

    def test_escape_hatch_reaches_legacy_handler(self) -> None:
        parser = Mock()
        args = argparse.Namespace(command="demo")
        parser.parse_args.return_value = args

        with (
            patch.dict(
                os.environ,
                {UNSAFE_LEGACY_MUTATIONS_ENV: "1"},
                clear=False,
            ),
            patch("agentsassemble.cli.build_parser", return_value=parser),
            patch(
                "agentsassemble.cli.run_demo_command",
                return_value=17,
            ) as handler,
        ):
            result = cli.main([])

        self.assertEqual(result, 17)
        handler.assert_called_once_with(
            args,
            run_demo_meeting=cli.run_demo_meeting,
        )

    def test_current_command_dispatch_is_unchanged(self) -> None:
        parser = Mock()
        args = argparse.Namespace(command="frontend-info")
        parser.parse_args.return_value = args

        with (
            patch.dict(
                os.environ,
                {UNSAFE_LEGACY_MUTATIONS_ENV: ""},
                clear=False,
            ),
            patch("agentsassemble.cli.build_parser", return_value=parser),
            patch(
                "agentsassemble.cli.run_frontend_info_command",
                return_value=23,
            ) as handler,
        ):
            result = cli.main([])

        self.assertEqual(result, 23)
        handler.assert_called_once_with(args)


if __name__ == "__main__":
    unittest.main()
