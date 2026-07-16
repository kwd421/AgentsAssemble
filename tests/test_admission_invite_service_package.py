from __future__ import annotations

import unittest

import agentsassemble.room_invite_application as compatibility_service
from agentsassemble.admission import invite_service as owned_service


class AdmissionInviteServicePackageTests(unittest.TestCase):
    def test_root_module_exports_owned_invite_service(self) -> None:
        self.assertIs(
            compatibility_service.InviteApplicationService,
            owned_service.InviteApplicationService,
        )
        self.assertIs(
            compatibility_service.PreparedInviteAdmission,
            owned_service.PreparedInviteAdmission,
        )
        self.assertIs(
            compatibility_service.create_invite_record,
            owned_service.create_invite_record,
        )
        self.assertEqual(
            compatibility_service.SESSION_TOKEN_PREFIX,
            owned_service.SESSION_TOKEN_PREFIX,
        )


if __name__ == "__main__":
    unittest.main()
