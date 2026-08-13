from __future__ import annotations

import json
import unittest

from agentsassemble.providers.capabilities import ProviderCapabilityCatalog


class CodexCatalogDiscoveryTests(unittest.TestCase):
    def test_live_cli_models_are_available_for_agent_creation(self):
        live_model = {
            "slug": "gpt-daybreak-blue-latest",
            "display_name": "Daybreak Blue",
            "supported_reasoning_levels": [{"effort": "low"}],
        }
        bundled_model = {
            "slug": "bundled-only-model",
            "display_name": "Bundled only",
            "supported_reasoning_levels": [{"effort": "low"}],
        }

        def runner(command: list[str], _timeout: float):
            models = [bundled_model] if command[-1:] == ["--bundled"] else [live_model]
            return 0, json.dumps({"models": models}), ""

        catalog = ProviderCapabilityCatalog(
            runner=runner,
            resolver=lambda executable: "/bin/codex" if executable == "codex" else None,
            remote_model_discovery=lambda _profile, _api_key: [],
        )

        providers = catalog.payload(refresh=True)
        codex = next(provider for provider in providers if provider["id"] == "codex")
        model_control = next(
            control for control in codex["controls"] if control["key"] == "model"
        )
        model_ids = [option["value"] for option in model_control["options"]]

        self.assertIn("gpt-daybreak-blue-latest", model_ids)
        self.assertNotIn("bundled-only-model", model_ids)


if __name__ == "__main__":
    unittest.main()
