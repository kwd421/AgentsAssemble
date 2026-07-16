from __future__ import annotations

import unittest

import agentsassemble.application_transaction as compatibility_transaction
import agentsassemble.gui_application as compatibility_gui
from agentsassemble.application import gui as owned_gui
from agentsassemble.application import transaction as owned_transaction


class ApplicationPackageTests(unittest.TestCase):
    def test_gui_application_root_module_exports_owned_services(self) -> None:
        self.assertIs(
            compatibility_gui.GuiApplicationServices,
            owned_gui.GuiApplicationServices,
        )
        self.assertIs(
            compatibility_gui.ApplicationDatabase,
            owned_gui.ApplicationDatabase,
        )

    def test_transaction_root_module_exports_owned_boundary(self) -> None:
        self.assertIs(
            compatibility_transaction.ApplicationTransactionBoundary,
            owned_transaction.ApplicationTransactionBoundary,
        )


if __name__ == "__main__":
    unittest.main()
