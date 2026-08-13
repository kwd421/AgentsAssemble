from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from agentsassemble.providers.capabilities import ProviderCapabilityCatalog
from agentsassemble.providers.launch_specs import native_cli_provider_definition
from agentsassemble.providers.process_environment import (
    ensure_provider_cli_search_path,
    sanitized_provider_environment,
)


class ProviderCliSearchPathTests(unittest.TestCase):
    def test_existing_user_local_bins_are_prepended_to_path(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            local_bin = home_path / ".local" / "bin"
            grok_bin = home_path / ".grok" / "bin"
            local_bin.mkdir(parents=True)
            grok_bin.mkdir(parents=True)
            environ = {"PATH": "/usr/bin", "HOME": home}

            updated = ensure_provider_cli_search_path(environ, home=home_path)

            parts = updated.split(os.pathsep)
            self.assertIn(str(local_bin), parts)
            self.assertIn(str(grok_bin), parts)
            self.assertEqual(environ["PATH"], updated)
            self.assertIn("/usr/bin", parts)

    def test_missing_user_bins_do_not_change_existing_path(self) -> None:
        environment = sanitized_provider_environment(
            source={
                "HOME": "/home/test",
                "PATH": "/bin",
                "CEREBRAS_API_KEY": "secret",
            }
        )
        self.assertEqual(environment["PATH"], "/bin")
        self.assertNotIn("CEREBRAS_API_KEY", environment)

    def test_freebuff_catalog_is_startable_without_models_cli(self) -> None:
        catalog = ProviderCapabilityCatalog(
            resolver=lambda name: f"/fake/{name}" if name == "freebuff" else None,
            runner=lambda _command, _timeout: (1, "", "not used"),
        )
        definition = native_cli_provider_definition("freebuff")
        assert definition is not None

        payload = catalog._native_payload(definition)

        self.assertTrue(payload["available"])
        self.assertTrue(payload["startable"])
        self.assertEqual(payload["discovery_status"], "ready")
        model = next(
            control for control in payload["controls"] if control["key"] == "model"
        )
        self.assertEqual(model["default_value"], "DeepSeek V4 Flash")


if __name__ == "__main__":
    unittest.main()
