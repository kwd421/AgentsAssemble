from __future__ import annotations

import unittest

import agentsassemble.application_transaction as compatibility_transaction
import agentsassemble.gui_application as compatibility_gui
import agentsassemble.room_agent_bridge as compatibility_agent_bridge
from agentsassemble.application import agent_bridge_entrypoint as owned_agent_bridge_entrypoint
from agentsassemble.application import gui as owned_gui
from agentsassemble.application import gui_factory as owned_gui_factory
from agentsassemble.application import transaction as owned_transaction


class ApplicationPackageTests(unittest.TestCase):
    def test_agent_bridge_root_command_uses_application_entrypoint(self) -> None:
        self.assertIs(
            compatibility_agent_bridge.main,
            owned_agent_bridge_entrypoint.main,
        )

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

    def test_gui_factory_is_owned_by_the_application_package(self) -> None:
        self.assertTrue(callable(owned_gui_factory.build_gui_application_services))
        self.assertTrue(hasattr(owned_gui_factory, "GuiRuntimeConstructors"))


if __name__ == "__main__":
    unittest.main()
