"""Compatibility entry point for the split GUI server test modules.

Normal discovery loads each domain module directly. A targeted legacy command
(``python -m unittest tests.test_gui_server``) receives the combined suite so it
cannot report a false-green zero-test result.
"""

from __future__ import annotations

import importlib
import unittest


_DOMAIN_MODULES = (
    "room_routes",
    "discovery_workroom",
    "meeting_payload",
    "process_smoke",
    "smoke_routes",
    "real_session_smoke",
    "readiness_probes",
    "health",
    "health_processes",
    "health_probes",
    "server_lifecycle",
    "roster",
    "room_payload",
    "turns",
    "session_lifecycle",
    "session_runs",
    "session_run_monitor",
    "session_recovery",
    "moderation_finalization",
    "lobby_social",
    "social_http",
    "streams_http",
    "provider_http",
)


def load_tests(
    loader: unittest.TestLoader,
    tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    if pattern is not None and pattern != "test_gui_server.py":
        return tests

    suite = unittest.TestSuite()
    for suffix in _DOMAIN_MODULES:
        module = importlib.import_module(f"tests.test_gui_server_{suffix}")
        suite.addTests(loader.loadTestsFromModule(module))
    return suite


if __name__ == "__main__":
    unittest.main()
