from __future__ import annotations

import unittest

import agentsassemble.provider_runtime_contracts as compatibility_contracts
from agentsassemble.providers import runtime_contracts as owned_contracts


class ProviderPackageTests(unittest.TestCase):
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
