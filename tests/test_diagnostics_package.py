from __future__ import annotations

import unittest

import agentsassemble.cleanup_report as compatibility_cleanup
from agentsassemble.diagnostics import cleanup as owned_cleanup


class DiagnosticsPackageTests(unittest.TestCase):
    def test_cleanup_report_root_module_exports_owned_diagnostics(self) -> None:
        for name in (
            "CleanupFailure",
            "CleanupReport",
            "emit_cleanup_failure",
        ):
            self.assertIs(
                getattr(compatibility_cleanup, name),
                getattr(owned_cleanup, name),
            )


if __name__ == "__main__":
    unittest.main()
