from __future__ import annotations

import threading
import unittest

from agentsassemble.providers.bridge_protocol import BridgeReportTimeout
from agentsassemble.providers.provider_requests import BridgeProviderRequestRouter


class ProviderRequestRouterTests(unittest.TestCase):
    def test_close_report_failure_reaches_the_provider_turn(self):
        responses: list[dict[str, object]] = []
        routers: list[BridgeProviderRequestRouter] = []

        def report(action: str, payload: dict[str, object]):
            if action == "provider.request.open":
                resolved = routers[0].resolve(
                    {
                        "provider_request_id": payload["provider_request_id"],
                        "option_id": "allow_once",
                    }
                )
                self.assertTrue(resolved)
                return {"status": "open"}
            if action == "provider.request.closed":
                raise BridgeReportTimeout(
                    request_id="command-close",
                    action=action,
                )
            self.fail(f"unexpected provider request report: {action}")

        router = BridgeProviderRequestRouter(
            report=report,
            stopping=threading.Event(),
        )
        routers.append(router)
        request = {
            "response_kind": "option",
            "options": [
                {"id": "allow_once", "kind": "allow_once"},
                {"id": "deny", "kind": "deny"},
            ],
        }

        with self.assertRaisesRegex(
            BridgeReportTimeout,
            "timed out waiting for ACK/NACK",
        ):
            router.handle(request, responses.append)

        self.assertEqual(responses, [{"option_id": "allow_once"}])
