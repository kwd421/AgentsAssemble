from __future__ import annotations

import unittest

import agentsassemble.provider_catalog as compatibility_catalog
import agentsassemble.provider_runtime_config as compatibility_config
import agentsassemble.provider_runtime_contracts as compatibility_contracts
from agentsassemble.providers import catalog as owned_catalog
from agentsassemble.providers import runtime_config as owned_config
from agentsassemble.providers import runtime_contracts as owned_contracts


class ProviderPackageTests(unittest.TestCase):
    def test_catalog_root_module_exports_owned_data_and_functions(self) -> None:
        self.assertIs(
            compatibility_catalog.PROVIDER_CATALOG,
            owned_catalog.PROVIDER_CATALOG,
        )
        self.assertIs(
            compatibility_catalog.catalog_payload,
            owned_catalog.catalog_payload,
        )

    def test_runtime_contract_root_module_exports_owned_types(self) -> None:
        self.assertIs(
            compatibility_contracts.AdapterContractError,
            owned_contracts.AdapterContractError,
        )
        self.assertIs(
            compatibility_contracts.ProviderTurnResult,
            owned_contracts.ProviderTurnResult,
        )
        self.assertIs(
            compatibility_contracts.ProviderRuntimeHealth,
            owned_contracts.ProviderRuntimeHealth,
        )
        self.assertIs(
            compatibility_contracts.SUPPORTED_DECLINE_REASONS,
            owned_contracts.SUPPORTED_DECLINE_REASONS,
        )

    def test_runtime_config_root_module_exports_owned_types(self) -> None:
        self.assertIs(
            compatibility_config.ProviderRuntimeConfigError,
            owned_config.ProviderRuntimeConfigError,
        )
        self.assertIs(
            compatibility_config.BridgeConfigError,
            owned_config.BridgeConfigError,
        )
        self.assertIs(
            compatibility_config.ProviderRuntimeProfile,
            owned_config.ProviderRuntimeProfile,
        )
        self.assertIs(
            compatibility_config.ProviderRuntimeConfig,
            owned_config.ProviderRuntimeConfig,
        )
        self.assertIs(
            compatibility_config.CanonicalBridgeLaunchConfig,
            owned_config.CanonicalBridgeLaunchConfig,
        )


if __name__ == "__main__":
    unittest.main()
