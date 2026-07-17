from __future__ import annotations

import unittest

import agentsassemble.frontend_runtime as compatibility_frontend_runtime
import agentsassemble.gui_response as compatibility_response
import agentsassemble.gui_request_security as compatibility_security
import agentsassemble.gui_router as compatibility_router
import agentsassemble.gui_static_transport as compatibility_static
import agentsassemble.gui_ws_http as compatibility_websocket
import agentsassemble.sse_cadence as compatibility_sse_cadence
from agentsassemble.web import response as owned_response
from agentsassemble.web import frontend_runtime as owned_frontend_runtime
from agentsassemble.web import router as owned_router
from agentsassemble.web import security as owned_security
from agentsassemble.web import sse_cadence as owned_sse_cadence
from agentsassemble.web import static as owned_static
from agentsassemble.web import websocket as owned_websocket


class WebTransportPackageTests(unittest.TestCase):
    def test_frontend_runtime_root_module_exports_owned_contract(self) -> None:
        for name in (
            "FrontendDistStatus",
            "default_frontend_dist_root",
            "frontend_dist_status",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(compatibility_frontend_runtime, name),
                    getattr(owned_frontend_runtime, name),
                )
        self.assertEqual(
            compatibility_frontend_runtime.REACT_APP_BUILD_COMMAND,
            owned_frontend_runtime.REACT_APP_BUILD_COMMAND,
        )
        self.assertEqual(
            compatibility_frontend_runtime.REACT_APP_MISSING_BUILD_MESSAGE,
            owned_frontend_runtime.REACT_APP_MISSING_BUILD_MESSAGE,
        )

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

    def test_router_root_module_exports_owned_request_context(self) -> None:
        self.assertIs(
            compatibility_router.RequestContext,
            owned_router.RequestContext,
        )
        self.assertIs(
            compatibility_router.Router,
            owned_router.Router,
        )

    def test_websocket_root_module_exports_owned_transport(self) -> None:
        self.assertIs(
            compatibility_websocket.handle_ws_upgrade,
            owned_websocket.handle_ws_upgrade,
        )
        self.assertIs(
            compatibility_websocket.register_ws_ticket_route,
            owned_websocket.register_ws_ticket_route,
        )

    def test_sse_cadence_root_module_exports_owned_transport_values(self) -> None:
        self.assertEqual(
            compatibility_sse_cadence.SSE_EVENT_POLL_INTERVAL_SECONDS,
            owned_sse_cadence.SSE_EVENT_POLL_INTERVAL_SECONDS,
        )
        self.assertEqual(
            compatibility_sse_cadence.SSE_KEEPALIVE_INTERVAL_SECONDS,
            owned_sse_cadence.SSE_KEEPALIVE_INTERVAL_SECONDS,
        )


if __name__ == "__main__":
    unittest.main()
