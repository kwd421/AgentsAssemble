from __future__ import annotations

import unittest

from agentsassemble.providers.capabilities import ProviderCapabilityCatalog
from agentsassemble.providers.harness_registry import (
    PUBLIC_HARNESS_IDS,
    catalog_harness_options,
    is_public_harness_id,
    require_harness_definition,
)
from agentsassemble.providers.launch_specs import native_cli_provider_definition


class HarnessRegistryTests(unittest.TestCase):
    def test_public_harness_ids_match_product_contract(self) -> None:
        self.assertEqual(
            PUBLIC_HARNESS_IDS,
            ("builtin", "codex", "claude", "opencode", "pi"),
        )
        for harness_id in PUBLIC_HARNESS_IDS:
            self.assertTrue(is_public_harness_id(harness_id))
            definition = require_harness_definition(harness_id)
            self.assertEqual(definition.id, harness_id)

    def test_catalog_lists_installed_harnesses_without_fallback_labels(self) -> None:
        available = {"codex", "claude", "opencode", "pi"}
        options = catalog_harness_options(
            resolver=lambda name: f"/bin/{name}" if name in available else None
        )
        self.assertEqual(
            [option["value"] for option in options],
            ["builtin", "codex", "claude", "opencode", "pi"],
        )
        pi = next(option for option in options if option["value"] == "pi")
        self.assertIn("approvals", pi["metadata"]["unsupported"])

    def test_api_catalog_exposes_registry_harnesses(self) -> None:
        available = {"codex", "claude", "opencode", "pi"}
        catalog = ProviderCapabilityCatalog(
            runner=lambda _command, _timeout: (1, "", "not installed"),
            resolver=lambda executable: (
                f"/bin/{executable}" if executable in available else None
            ),
            remote_model_discovery=lambda _profile, _api_key: [],
            secret_resolver=lambda _provider_id: "",
        )
        deepseek = next(
            provider
            for provider in catalog.payload(refresh=True)
            if provider["id"] == "deepseek"
        )
        harness = next(
            control
            for control in deepseek["controls"]
            if control["key"] == "execution_harness"
        )
        self.assertEqual(
            [option["value"] for option in harness["options"]],
            ["builtin", "codex", "claude", "opencode", "pi"],
        )

    def test_deepseek_accepts_opencode_and_pi_execution_harness(self) -> None:
        definition = native_cli_provider_definition("deepseek")
        self.assertIsNotNone(definition)
        for harness in ("opencode", "pi"):
            spec = definition.make_selected_spec(
                agent_id="agent-1",
                display_name="DeepSeek",
                cwd=".",
                model=definition.default_model,
                reasoning_effort=definition.default_reasoning_effort,
                service_tier=definition.default_service_tier,
                variant=definition.default_variant,
                execution_harness=harness,
                permission_mode=definition.default_permission_mode,
            )
            self.assertEqual(spec.execution_harness, harness)


if __name__ == "__main__":
    unittest.main()
