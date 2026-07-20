import unittest

from agentsassemble.gui_legacy_lobby_http import (
    register_legacy_lobby_routes as compatibility_lobby_register,
)
from agentsassemble.gui_legacy_meeting_http import (
    register_legacy_meeting_routes as compatibility_meeting_register,
)
from agentsassemble.legacy.meeting.http.lobby import register_legacy_lobby_routes
from agentsassemble.legacy.meeting.http.meeting import register_legacy_meeting_routes


class LegacyMeetingHttpPackageTests(unittest.TestCase):
    def test_root_lobby_module_exports_owned_registrar(self) -> None:
        self.assertIs(compatibility_lobby_register, register_legacy_lobby_routes)

    def test_root_meeting_module_exports_owned_registrar(self) -> None:
        self.assertIs(compatibility_meeting_register, register_legacy_meeting_routes)


if __name__ == "__main__":
    unittest.main()
