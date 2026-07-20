from __future__ import annotations

import unittest

import agentsassemble.cleanup_report as compatibility_cleanup
import agentsassemble.canonical_room_benchmark as compatibility_canonical_benchmark
import agentsassemble.cli_diagnostics as compatibility_cli
import agentsassemble.diagnostic_report_projection as compatibility_projection
import agentsassemble.live_cli_smoke as compatibility_live_cli_smoke
from agentsassemble.diagnostics import canonical_room_benchmark as owned_canonical_benchmark
from agentsassemble.diagnostics import cli as owned_cli
from agentsassemble.diagnostics import cleanup as owned_cleanup
from agentsassemble.diagnostics import live_cli_smoke as owned_live_cli_smoke
from agentsassemble.diagnostics import report_projection as owned_projection


class DiagnosticsPackageTests(unittest.TestCase):
    def test_room_benchmark_root_exports_owned_diagnostics(self) -> None:
        self.assertIs(
            compatibility_canonical_benchmark.run_canonical_room_benchmark,
            owned_canonical_benchmark.run_canonical_room_benchmark,
        )

    def test_cli_root_exports_owned_diagnostics(self) -> None:
        self.assertIs(compatibility_cli.DiagnosticCliRuntime, owned_cli.DiagnosticCliRuntime)

    def test_live_cli_smoke_root_exports_owned_diagnostics(self) -> None:
        self.assertIs(
            compatibility_live_cli_smoke.run_live_cli_smoke,
            owned_live_cli_smoke.run_live_cli_smoke,
        )

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
