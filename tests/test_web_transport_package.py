from __future__ import annotations

import unittest

import agentsassemble.gui_request_security as compatibility_security
from agentsassemble.web import security as owned_security


class WebTransportPackageTests(unittest.TestCase):
    def test_request_security_root_module_exports_owned_policy(self) -> None:
        self.assertIs(
            compatibility_security._request_trusted,
            owned_security._request_trusted,
        )
        self.assertIs(
            compatibility_security._public_invite_route_allowed,
            owned_security._public_invite_route_allowed,
        )
        self.assertEqual(
            compatibility_security._LOOPBACK_HOSTNAMES,
            owned_security._LOOPBACK_HOSTNAMES,
        )


if __name__ == "__main__":
    unittest.main()
