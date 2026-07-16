from __future__ import annotations

import unittest

import agentsassemble.provider_catalog as compatibility_catalog
import agentsassemble.provider_runtime_contracts as compatibility_contracts
from agentsassemble.providers import catalog as owned_catalog
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


if __name__ == "__main__":
    unittest.main()
