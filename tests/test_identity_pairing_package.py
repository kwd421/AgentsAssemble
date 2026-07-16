from __future__ import annotations

import unittest

import agentsassemble.operator_pairing as compatibility_pairing
from agentsassemble.identity import pairing as owned_pairing


class IdentityPairingPackageTests(unittest.TestCase):
    def test_root_module_exports_owned_pairing_service(self) -> None:
        self.assertIs(
            compatibility_pairing.OperatorPairingService,
            owned_pairing.OperatorPairingService,
        )
        self.assertIs(
            compatibility_pairing.normalize_pairing_origin,
            owned_pairing.normalize_pairing_origin,
        )
        self.assertEqual(
            compatibility_pairing.OPERATOR_PAIRING_TOKEN_PREFIX,
            owned_pairing.OPERATOR_PAIRING_TOKEN_PREFIX,
        )


if __name__ == "__main__":
    unittest.main()
