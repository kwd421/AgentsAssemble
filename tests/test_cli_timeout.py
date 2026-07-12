"""Compatibility entry point for the split CLI timeout test modules.

Normal discovery loads each domain module directly. A targeted legacy command
(``python -m unittest tests.test_cli_timeout``) receives the combined suite so
it cannot report a false-green zero-test result.
"""

from __future__ import annotations

import importlib
import unittest


_DOMAIN_MODULES = (
    "call",
    "core",
    "delegate",
    "diagnostics",
    "operations",
    "presence",
    "processes",
    "room",
    "run",
    "run_group",
    "runtime_process",
    "session_controls",
    "session_ensure",
    "session_runs",
    "session_start",
)


def load_tests(
    loader: unittest.TestLoader,
    tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    if pattern is not None and pattern != "test_cli_timeout.py":
        return tests

    suite = unittest.TestSuite()
    for suffix in _DOMAIN_MODULES:
        module = importlib.import_module(f"tests.test_cli_timeout_{suffix}")
        suite.addTests(loader.loadTestsFromModule(module))
    return suite
