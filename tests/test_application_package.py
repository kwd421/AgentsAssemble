from __future__ import annotations

import unittest
from pathlib import Path

import agentsassemble.application_transaction as compatibility_transaction
import agentsassemble.gui_application as compatibility_gui
import agentsassemble.room_agent_bridge as compatibility_agent_bridge
import agentsassemble.room_users as compatibility_room_users
import agentsassemble.session_run_monitor as compatibility_session_run_monitor
import agentsassemble.stable_entry as compatibility_stable_entry
import agentsassemble.public_invite_runtime as compatibility_public_invite_runtime
import agentsassemble.public_tunnel as compatibility_public_tunnel
from agentsassemble.application import agent_bridge_entrypoint as owned_agent_bridge_entrypoint
from agentsassemble.application import gui as owned_gui
from agentsassemble.application import gui_factory as owned_gui_factory
from agentsassemble.application import room_users as owned_room_users
from agentsassemble.application import session_run_monitor as owned_session_run_monitor
from agentsassemble.application import stable_entry as owned_stable_entry
from agentsassemble.application import public_invite_runtime as owned_public_invite_runtime
from agentsassemble.application import public_tunnel as owned_public_tunnel
from agentsassemble.application import transaction as owned_transaction


ROOT = Path(__file__).resolve().parents[1]


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

    def test_room_users_root_module_exports_owned_facade(self) -> None:
        self.assertIs(
            compatibility_room_users.configure_room_users_backend,
            owned_room_users.configure_room_users_backend,
        )
        self.assertIs(
            compatibility_room_users.resolve_device_user,
            owned_room_users.resolve_device_user,
        )

    def test_session_run_monitor_root_module_exports_owned_lifecycle(self) -> None:
        self.assertIs(
            compatibility_session_run_monitor.PeriodicSessionRunMonitor,
            owned_session_run_monitor.PeriodicSessionRunMonitor,
        )
        self.assertIs(
            compatibility_session_run_monitor.normalized_monitor_interval,
            owned_session_run_monitor.normalized_monitor_interval,
        )

    def test_stable_entry_root_module_exports_owned_service(self) -> None:
        self.assertIs(
            compatibility_stable_entry.stable_entry_url,
            owned_stable_entry.stable_entry_url,
        )
        self.assertIs(
            compatibility_stable_entry.announce_stable_entry,
            owned_stable_entry.announce_stable_entry,
        )
        self.assertEqual(
            owned_stable_entry._REDIRECTOR_DIR,
            ROOT / "infra" / "room-redirector",
        )

    def test_public_invite_runtime_root_module_exports_owned_service(self) -> None:
        self.assertIs(
            compatibility_public_invite_runtime.PublicInviteRuntime,
            owned_public_invite_runtime.PublicInviteRuntime,
        )
        self.assertIs(
            compatibility_public_invite_runtime.normalize_public_room_url,
            owned_public_invite_runtime.normalize_public_room_url,
        )

    def test_public_tunnel_root_module_exports_owned_manager(self) -> None:
        self.assertIs(
            compatibility_public_tunnel.PublicTunnelManager,
            owned_public_tunnel.PublicTunnelManager,
        )
        self.assertIs(
            compatibility_public_tunnel.extract_trycloudflare_url,
            owned_public_tunnel.extract_trycloudflare_url,
        )


if __name__ == "__main__":
    unittest.main()
