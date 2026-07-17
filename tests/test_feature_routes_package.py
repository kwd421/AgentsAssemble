from __future__ import annotations

import unittest

import agentsassemble.side_chat as compatibility_side_chat
import agentsassemble.room_friends as compatibility_room_friends

from agentsassemble.features.mafia.routes import (
    OperationPayloadReader as OwnedOperationPayloadReader,
)
from agentsassemble.features.mafia.routes import (
    register_mafia_routes as owned_register_mafia_routes,
)
from agentsassemble.features.side_chat.routes import (
    register_side_chat_routes as owned_register_side_chat_routes,
)
from agentsassemble.features.side_chat import service as owned_side_chat
from agentsassemble.features.social.routes import (
    register_room_friend_profile_routes as owned_register_social_routes,
)
from agentsassemble.features.social import friends as owned_room_friends
from agentsassemble.gui_mafia_http import (
    OperationPayloadReader as CompatibilityOperationPayloadReader,
)
from agentsassemble.gui_mafia_http import (
    register_mafia_routes as compatibility_register_mafia_routes,
)
from agentsassemble.gui_side_chat_http import (
    register_side_chat_routes as compatibility_register_side_chat_routes,
)
from agentsassemble.gui_social_http import (
    register_room_friend_profile_routes as compatibility_register_social_routes,
)


class FeatureRoutesPackageTests(unittest.TestCase):
    def test_root_route_modules_are_compatibility_exports(self) -> None:
        self.assertIs(
            CompatibilityOperationPayloadReader,
            OwnedOperationPayloadReader,
        )
        self.assertIs(
            compatibility_register_mafia_routes,
            owned_register_mafia_routes,
        )
        self.assertIs(
            compatibility_register_side_chat_routes,
            owned_register_side_chat_routes,
        )
        self.assertIs(
            compatibility_register_social_routes,
            owned_register_social_routes,
        )

    def test_side_chat_root_module_exports_owned_service(self) -> None:
        self.assertIs(
            compatibility_side_chat.append_side_chat_event,
            owned_side_chat.append_side_chat_event,
        )
        self.assertIs(
            compatibility_side_chat.read_side_chat,
            owned_side_chat.read_side_chat,
        )

    def test_room_friends_root_module_exports_owned_service(self) -> None:
        for name in (
            "read_room_friends",
            "upsert_room_friend",
            "delete_room_friend",
            "room_friends_payload",
            "room_friend_type_for_agent",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_room_friends, name),
                    getattr(owned_room_friends, name),
                )


if __name__ == "__main__":
    unittest.main()
