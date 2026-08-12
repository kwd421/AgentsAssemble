import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import Mock
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agentsassemble.providers.bridges.claude_code_bridge import (
    CLAUDE_PRINT_MODE_DISABLED_MESSAGE,
    _handler,
    require_bridge_token,
    run_bridge_request,
)


class ClaudeCodeBridgeTests(unittest.TestCase):
    def test_bridge_fails_closed_without_running_claude_print_mode(self):
        runner = Mock()
        payload = {
            "step": "round",
            "role": {"id": "fanboard_skeptic", "display_name": "만갤러"},
            "prompt": "Return only JSON",
        }

        response = run_bridge_request(payload, command="claude", runner=runner)

        self.assertEqual(response["text"], CLAUDE_PRINT_MODE_DISABLED_MESSAGE)
        self.assertEqual(response["metadata"]["command"], "disabled")
        self.assertEqual(response["metadata"]["role_id"], "fanboard_skeptic")
        runner.assert_not_called()

    def test_bridge_requires_token_before_serving(self):
        with self.assertRaisesRegex(ValueError, "requires --token"):
            require_bridge_token(None)

    def test_bridge_health_endpoint_requires_auth_and_does_not_run_claude(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(token="bridge-token", command="claude"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            health_url = f"http://127.0.0.1:{server.server_port}/agentsassemble/health"

            with self.assertRaises(HTTPError) as unauthorized:
                urlopen(health_url, timeout=4)
            self.assertEqual(unauthorized.exception.code, 401)
            unauthorized.exception.read()
            unauthorized.exception.close()

            request = Request(health_url, headers={"Authorization": "Bearer bridge-token"}, method="GET")
            with patch(
                "agentsassemble.providers.bridges.claude_code_bridge.run_bridge_request",
                side_effect=AssertionError("health check must not run Claude"),
            ):
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["bridge"], "claude_code")
        self.assertEqual(payload["health_endpoint"], "/agentsassemble/health")
        self.assertEqual(payload["run_endpoint"], "/agentsassemble/run")

if __name__ == "__main__":
    unittest.main()
