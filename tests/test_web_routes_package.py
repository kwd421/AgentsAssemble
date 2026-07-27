from __future__ import annotations

import unittest

import agentsassemble.gui_attachment_http as compatibility_attachments
import agentsassemble.gui_observability_http as compatibility_observability
import agentsassemble.gui_provider_http as compatibility_providers
import agentsassemble.gui_public_invite_http as compatibility_public_invite
import agentsassemble.gui_retired_http as compatibility_retired
import agentsassemble.gui_room_agent_http as compatibility_agent_sessions
import agentsassemble.gui_room_invite_http as compatibility_room_invite
import agentsassemble.legacy.meeting.http.room_lifecycle_compat as compatibility_room_lifecycle
import agentsassemble.legacy.meeting.http.room_moderation_media as compatibility_moderation_media
import agentsassemble.gui_room_settings_http as compatibility_room_settings
from agentsassemble.web.routes import agent_sessions as owned_agent_sessions
from agentsassemble.web.routes import attachments as owned_attachments
from agentsassemble.web.routes import observability as owned_observability
from agentsassemble.web.routes import providers as owned_providers
from agentsassemble.web.routes import public_invite as owned_public_invite
from agentsassemble.web.routes import retired as owned_retired
from agentsassemble.web.routes import room_history as owned_room_history
from agentsassemble.web.routes import room_invite as owned_room_invite
from agentsassemble.web.routes import room_lifecycle as owned_room_lifecycle
from agentsassemble.web.routes import room_members as owned_room_members
from agentsassemble.web.routes import room_settings as owned_room_settings
from agentsassemble.web.router import Router


class WebRoutesPackageTests(unittest.TestCase):
    def test_agent_session_root_module_exports_owned_routes(self) -> None:
        self.assertIs(
            compatibility_agent_sessions.register_agent_session_routes,
            owned_agent_sessions.register_agent_session_routes,
        )

    def test_attachment_root_module_exports_owned_routes(self) -> None:
        self.assertIs(
            compatibility_attachments.register_attachment_routes,
            owned_attachments.register_attachment_routes,
        )

    def test_observability_root_module_exports_owned_routes(self) -> None:
        self.assertIs(
            compatibility_observability.register_observability_routes,
            owned_observability.register_observability_routes,
        )
        self.assertIs(
            compatibility_observability.ProcessSnapshotSource,
            owned_observability.ProcessSnapshotSource,
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

    def test_retired_root_module_exports_owned_tombstones(self) -> None:
        self.assertIs(
            compatibility_retired.register_retired_legacy_routes,
            owned_retired.register_retired_legacy_routes,
        )

    def test_room_invite_root_module_exports_owned_routes(self) -> None:
        self.assertIs(
            compatibility_room_invite.register_invite_admission_routes,
            owned_room_invite.register_invite_admission_routes,
        )

    def test_room_lifecycle_root_module_exports_owned_route_parts(self) -> None:
        self.assertIs(
            compatibility_room_lifecycle.register_room_history_routes,
            owned_room_history.register_room_history_routes,
        )
        self.assertIs(
            compatibility_room_lifecycle.register_current_room_lifecycle_routes,
            owned_room_lifecycle.register_room_lifecycle_routes,
        )

    def test_room_lifecycle_compatibility_registrar_keeps_ensure_and_current_routes(
        self,
    ) -> None:
        router = Router()

        compatibility_room_lifecycle.register_room_lifecycle_routes(router)

        self.assertIn(("POST", "/api/room/ensure"), router.routes())
        self.assertIn(("POST", "/api/rooms/close"), router.routes())

    def test_moderation_root_module_exports_owned_member_routes(self) -> None:
        self.assertIs(
            compatibility_moderation_media.register_room_member_routes,
            owned_room_members.register_room_member_routes,
        )
        self.assertIs(
            compatibility_moderation_media.room_members_response,
            owned_room_members.room_members_response,
        )

    def test_moderation_compatibility_registrar_keeps_member_and_media_routes(
        self,
    ) -> None:
        router = Router()

        compatibility_moderation_media.register_moderation_media_routes(
            router,
            speech_rejection_status=lambda _category: 400,
        )

        self.assertIn(("GET", "/api/room-members"), router.routes())
        self.assertIn(("POST", "/api/room-members/kick"), router.routes())
        self.assertIn(("GET", "/api/room/voice"), router.routes())

    def test_room_settings_root_module_exports_owned_routes(self) -> None:
        self.assertIs(
            compatibility_room_settings.register_room_settings_routes,
            owned_room_settings.register_room_settings_routes,
        )


if __name__ == "__main__":
    unittest.main()
