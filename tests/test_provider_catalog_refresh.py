from __future__ import annotations

import subprocess
import unittest

from agentsassemble.providers.capabilities import (
    DEFAULT_CATALOG_TTL_SECONDS,
    ProviderCapabilityCatalog,
)


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


class ProviderCatalogTtlTests(unittest.TestCase):
    """Discovery is expensive; it must not run on a short timer."""

    def _catalog(self, probes: list[int], **kwargs) -> ProviderCapabilityCatalog:
        def runner(command: list[str], timeout: float):
            del timeout
            probes.append(1)
            return 0, "gemini-3.6-flash-low\n", ""

        return ProviderCapabilityCatalog(
            runner=runner,
            resolver=lambda executable: "/bin/agy" if executable == "agy" else None,
            **kwargs,
        )

    def test_default_expiry_is_daily_not_minutes(self) -> None:
        # A pass spawns every provider CLI and fetches two remote model lists
        # (~8s measured). Model catalogs do not move on that timescale.
        self.assertGreaterEqual(DEFAULT_CATALOG_TTL_SECONDS, 24 * 60 * 60)

    def test_repeat_reads_inside_the_window_do_not_rediscover(self) -> None:
        probes: list[int] = []
        catalog = self._catalog(probes)
        catalog.snapshot(refresh=True)
        discovered = len(probes)

        for _ in range(5):
            catalog.snapshot()

        self.assertEqual(len(probes), discovered, "cached reads must not probe again")

    def test_an_explicit_refresh_still_rediscovers(self) -> None:
        # The manual button and the post-login hook rely on this staying true.
        probes: list[int] = []
        catalog = self._catalog(probes)
        catalog.snapshot(refresh=True)
        discovered = len(probes)

        catalog.snapshot(refresh=True)

        self.assertGreater(len(probes), discovered)
