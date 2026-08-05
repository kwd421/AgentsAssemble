from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.providers.capabilities import ProviderCapabilityCatalog


class GrokCatalogTests(unittest.TestCase):
    def test_user_configured_models_are_not_exposed_as_subscription_models(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            config_dir = home / ".grok"
            config_dir.mkdir()
            (config_dir / "config.toml").write_text(
                "\n".join(
                    (
                        "[model.ocx-gpt-5-6-sol]",
                        'base_url = "https://example.invalid/v1"',
                        "",
                        "[model.ocx-agentsassemble-deepseek-v4-flash]",
                        'base_url = "https://example.invalid/v1"',
                    )
                ),
                encoding="utf-8",
            )

            with patch("pathlib.Path.home", return_value=home):
                catalog = ProviderCapabilityCatalog(
                    runner=lambda _command, _timeout: (
                        0,
                        "\n".join(
                            (
                                "Default model: grok-future-native",
                                "Available models:",
                                "* grok-future-native",
                                "- ocx-gpt-5-6-sol",
                                "- ocx-agentsassemble-deepseek-v4-flash",
                            )
                        ),
                        "",
                    ),
                    resolver=lambda executable: "/bin/grok" if executable == "grok" else None,
                )

                grok = next(
                    provider
                    for provider in catalog.payload(refresh=True)
                    if provider["id"] == "grok"
                )

        model = next(control for control in grok["controls"] if control["key"] == "model")
        self.assertEqual(
            [option["value"] for option in model["options"]],
            ["grok-future-native"],
        )


if __name__ == "__main__":
    unittest.main()
