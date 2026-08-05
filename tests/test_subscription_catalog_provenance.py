from __future__ import annotations

import json
import unittest

from agentsassemble.providers.capabilities import ProviderCapabilityCatalog


class SubscriptionCatalogProvenanceTests(unittest.TestCase):
    def test_subscription_catalogs_keep_future_native_models_without_cross_provider_leaks(self):
        def runner(command: list[str], _timeout: float):
            executable = command[0].rsplit("/", 1)[-1]
            if executable == "codex":
                return 0, json.dumps(
                    {
                        "models": [
                            {
                                "slug": "gpt-future-native",
                                "display_name": "GPT Future Native",
                                "supported_reasoning_levels": [{"effort": "low"}],
                                "service_tiers": [],
                            }
                        ]
                    }
                ), ""
            if executable == "agy":
                return 0, "gemini-future-native-low\n", ""
            if executable == "grok":
                return 0, (
                    "Default model: grok-future-native\n"
                    "Available models:\n"
                    "* grok-future-native\n"
                    "- user-gateway-model\n"
                ), ""
            if executable == "claude":
                return 0, "Claude help", ""
            if executable == "cursor-agent":
                return 0, "future-native - Future Native\n", ""
            if executable == "opencode":
                return 0, (
                    "opencode/future-zen\n{}\n"
                    "opencode-go/future-go\n{}\n"
                    "personal-provider/private-model\n{}\n"
                ), ""
            if executable == "ollama" and command[1:] == ["list"]:
                return 0, (
                    "NAME                 ID       SIZE    MODIFIED\n"
                    "future-cloud:cloud   abc123   -       now\n"
                ), ""
            if executable == "ollama" and command[1] == "show":
                return 0, "Capabilities\n    tools\n", ""
            return 1, "", "unsupported"

        catalog = ProviderCapabilityCatalog(
            runner=runner,
            resolver=lambda executable: (
                None if executable == "lms" else f"/bin/{executable}"
            ),
            claude_model_discovery=lambda _executable: ["claude-sonnet-9-9"],
            claude_xhigh_model_discovery=lambda _executable: [],
            remote_model_discovery=lambda _profile, _api_key: [],
            grok_custom_model_discovery=lambda: {"user-gateway-model"},
        )

        providers = {
            str(provider["id"]): provider
            for provider in catalog.payload(refresh=True)
        }

        expected_models = {
            "codex": ["gpt-future-native"],
            "antigravity": ["gemini-future-native"],
            "grok": ["grok-future-native"],
            "claude": ["claude-sonnet-9-9"],
            "cursor": ["future-native"],
            "opencode": ["opencode/future-zen", "opencode-go/future-go"],
            "ollama": ["future-cloud:cloud"],
        }
        for provider_id, expected in expected_models.items():
            with self.subTest(provider=provider_id):
                provider = providers[provider_id]
                model_control = next(
                    control
                    for control in provider["controls"]
                    if control["key"] == "model"
                )
                self.assertEqual(
                    [option["value"] for option in model_control["options"]],
                    expected,
                )
                self.assertTrue(provider.get("model_catalog_provenance"))


if __name__ == "__main__":
    unittest.main()
