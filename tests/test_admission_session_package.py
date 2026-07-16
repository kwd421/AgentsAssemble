from __future__ import annotations

import unittest

import agentsassemble.room_session_issuer as compatibility_issuer
import agentsassemble.room_session_service as compatibility_service
from agentsassemble.admission import session_issuer as owned_issuer
from agentsassemble.admission import session_service as owned_service


class AdmissionSessionPackageTests(unittest.TestCase):
    def test_root_session_modules_export_owned_services(self) -> None:
        self.assertIs(
            compatibility_issuer.RoomSessionIssuer,
            owned_issuer.RoomSessionIssuer,
        )
        self.assertIs(
            compatibility_issuer.session_token_fingerprint,
            owned_issuer.session_token_fingerprint,
        )
        self.assertIs(
            compatibility_service.RoomSessionService,
            owned_service.RoomSessionService,
        )


if __name__ == "__main__":
    unittest.main()
