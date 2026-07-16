from __future__ import annotations

import unittest

from agentsassemble.features.mafia.routes import (
    OperationPayloadReader as OwnedOperationPayloadReader,
)
from agentsassemble.features.mafia.routes import (
    register_mafia_routes as owned_register_mafia_routes,
)
from agentsassemble.features.side_chat.routes import (
    register_side_chat_routes as owned_register_side_chat_routes,
)
from agentsassemble.features.social.routes import (
    register_room_friend_profile_routes as owned_register_social_routes,
)
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


if __name__ == "__main__":
    unittest.main()
