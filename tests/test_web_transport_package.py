from __future__ import annotations

import unittest

import agentsassemble.gui_response as compatibility_response
import agentsassemble.gui_request_security as compatibility_security
import agentsassemble.gui_static_transport as compatibility_static
from agentsassemble.web import response as owned_response
from agentsassemble.web import security as owned_security
from agentsassemble.web import static as owned_static


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

    def test_response_root_module_exports_owned_transport(self) -> None:
        self.assertIs(
            compatibility_response.GuiResponseMethods,
            owned_response.GuiResponseMethods,
        )
        self.assertIs(
            compatibility_response._sse_event,
            owned_response._sse_event,
        )

    def test_static_root_module_exports_owned_transport(self) -> None:
        self.assertIs(
            compatibility_static.ReactStaticTransport,
            owned_static.ReactStaticTransport,
        )
        self.assertIs(
            compatibility_static.safe_static_path,
            owned_static.safe_static_path,
        )


if __name__ == "__main__":
    unittest.main()
