from __future__ import annotations

import unittest

import agentsassemble.gui_attachment_http as compatibility_attachments
import agentsassemble.gui_provider_http as compatibility_providers
import agentsassemble.gui_public_invite_http as compatibility_public_invite
import agentsassemble.gui_room_invite_http as compatibility_room_invite
import agentsassemble.gui_room_settings_http as compatibility_room_settings
from agentsassemble.web.routes import attachments as owned_attachments
from agentsassemble.web.routes import providers as owned_providers
from agentsassemble.web.routes import public_invite as owned_public_invite
from agentsassemble.web.routes import room_invite as owned_room_invite
from agentsassemble.web.routes import room_settings as owned_room_settings


class WebRoutesPackageTests(unittest.TestCase):
    def test_attachment_root_module_exports_owned_routes(self) -> None:
        self.assertIs(
            compatibility_attachments.register_attachment_routes,
            owned_attachments.register_attachment_routes,
        )

    def test_provider_root_module_exports_owned_routes(self) -> None:
        self.assertIs(
            compatibility_providers.register_provider_routes,
            owned_providers.register_provider_routes,
        )
        self.assertIs(
            compatibility_providers.provider_catalog_payload,
            owned_providers.provider_catalog_payload,
        )

    def test_public_invite_root_module_exports_owned_routes(self) -> None:
        self.assertIs(
            compatibility_public_invite.register_public_invite_admin_routes,
            owned_public_invite.register_public_invite_admin_routes,
        )

    def test_room_invite_root_module_exports_owned_routes(self) -> None:
        self.assertIs(
            compatibility_room_invite.register_invite_admission_routes,
            owned_room_invite.register_invite_admission_routes,
        )

    def test_room_settings_root_module_exports_owned_routes(self) -> None:
        self.assertIs(
            compatibility_room_settings.register_room_settings_routes,
            owned_room_settings.register_room_settings_routes,
        )


if __name__ == "__main__":
    unittest.main()
