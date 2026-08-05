from __future__ import annotations

import unittest

from agentsassemble.providers.api_context import ApiContextPolicy
from agentsassemble.providers.runtime_factory import runtime_from_config
from tests.test_room_agent_bridge import _runtime_config


class ProviderContextContractTests(unittest.TestCase):
    def test_runtime_factory_applies_the_selected_catalog_context_contract(self) -> None:
        runtime = runtime_from_config(
            _runtime_config(
                provider_kind="openrouter_api",
                runtime_kind="api",
                transport="https",
                model="vendor/model",
                provider_endpoint="https://openrouter.ai/api/v1",
                reasoning_effort="",
                service_tier="",
                variant="",
                context_contract_bytes=180_000,
            ),
            credential="test-key",
        )

        self.assertEqual(
            runtime.health()["context_hard_limit_bytes"],
            ApiContextPolicy(180_000).hard_limit_bytes,
        )


if __name__ == "__main__":
    unittest.main()
