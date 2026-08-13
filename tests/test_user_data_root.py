"""Contract: CLI gui and desktop share one default on-disk product root."""

from __future__ import annotations

import unittest
from pathlib import Path

from agentsassemble.application.user_data_root import (
    OUTPUT_ROOT_ENV,
    default_output_root,
    resolve_output_root,
)


class UserDataRootTests(unittest.TestCase):
    def test_env_override_wins_over_platform_default(self) -> None:
        root = default_output_root(
            environ={OUTPUT_ROOT_ENV: "~/AgentsAssemble-Override"},
            home=Path("/Users/example"),
        )
        self.assertEqual(root, Path("~/AgentsAssemble-Override").expanduser())

    def test_empty_env_falls_back_to_macos_application_support(self) -> None:
        import sys

        if sys.platform != "darwin":
            self.skipTest("macOS path contract")
        root = default_output_root(environ={}, home=Path("/Users/example"))
        self.assertEqual(
            root,
            Path("/Users/example/Library/Application Support/AgentsAssemble"),
        )

    def test_resolve_blank_configured_uses_default(self) -> None:
        import sys

        if sys.platform != "darwin":
            self.skipTest("macOS path contract")
        root = resolve_output_root(
            "   ",
            environ={},
            home=Path("/Users/example"),
        )
        self.assertEqual(
            root,
            Path("/Users/example/Library/Application Support/AgentsAssemble"),
        )

    def test_resolve_explicit_path_is_unchanged_except_expanduser(self) -> None:
        root = resolve_output_root(
            "/var/tmp/aa-data",
            environ={OUTPUT_ROOT_ENV: "/ignored"},
            home=Path("/Users/example"),
        )
        self.assertEqual(root, Path("/var/tmp/aa-data"))


if __name__ == "__main__":
    unittest.main()
