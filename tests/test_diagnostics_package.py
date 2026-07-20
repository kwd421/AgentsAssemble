from __future__ import annotations

import unittest

import agentsassemble.cleanup_report as compatibility_cleanup
import agentsassemble.diagnostic_report_projection as compatibility_projection
from agentsassemble.diagnostics import cleanup as owned_cleanup
from agentsassemble.diagnostics import report_projection as owned_projection


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

    def test_report_projection_root_module_exports_owned_diagnostics(self) -> None:
        for name in (
            "looks_sensitive_operator_diagnostic_text",
            "safe_diagnostic_report_payload",
        ):
            self.assertIs(
                getattr(compatibility_projection, name),
                getattr(owned_projection, name),
            )


if __name__ == "__main__":
    unittest.main()
