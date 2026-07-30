from __future__ import annotations

import subprocess
import unittest

from agentsassemble.providers.capabilities import ProviderCapabilityCatalog


class ProviderCatalogRefreshTests(unittest.TestCase):
    def test_antigravity_timeout_keeps_last_verified_models_startable(self):
        antigravity_attempts = 0

        def runner(command: list[str], timeout: float):
            nonlocal antigravity_attempts
            if command[0].endswith("agy"):
                antigravity_attempts += 1
                if antigravity_attempts == 2:
                    raise subprocess.TimeoutExpired(command, timeout)
                return 0, "gemini-3.6-flash-low\n", ""
            return 1, "", "unsupported"

        catalog = ProviderCapabilityCatalog(
            runner=runner,
            resolver=lambda executable: "/bin/agy" if executable == "agy" else None,
        )
        first = catalog.snapshot(refresh=True)
        first_antigravity = next(
            provider for provider in first["providers"] if provider["id"] == "antigravity"
        )

        refreshed = catalog.snapshot(refresh=True)
        antigravity = next(
            provider for provider in refreshed["providers"] if provider["id"] == "antigravity"
        )

        self.assertEqual(antigravity["controls"], first_antigravity["controls"])
        self.assertTrue(antigravity["startable"])
        self.assertEqual(antigravity["discovery_status"], "ready")
        self.assertEqual(antigravity["catalog_source"], "stale_cache")
        self.assertEqual(antigravity["discovery_error_code"], "model_discovery_timeout")
        selected = catalog.validate_selection(
            catalog_revision=str(refreshed["catalog_revision"]),
            provider_id="antigravity",
            values={
                "model": "gemini-3.6-flash",
                "reasoning_effort": "low",
                "permission_mode": "meeting_read_only",
            },
        )
        self.assertEqual(selected.model, "gemini-3.6-flash")

    def test_cursor_authentication_failure_is_reported_as_a_supported_error(self):
        catalog = ProviderCapabilityCatalog(
            runner=lambda _command, _timeout: (
                1,
                "",
                "Error: Authentication required. Run 'agent login'.",
            ),
            resolver=lambda executable: "/bin/cursor-agent" if executable == "cursor-agent" else None,
        )

        snapshot = catalog.snapshot(refresh=True)
        cursor = next(provider for provider in snapshot["providers"] if provider["id"] == "cursor")

        self.assertFalse(cursor["startable"])
        self.assertEqual(cursor["discovery_status"], "failed")
        self.assertEqual(cursor["discovery_error_code"], "authentication_required")
        self.assertNotEqual(cursor["discovery_error"], "model discovery returned no supported options")


if __name__ == "__main__":
    unittest.main()
