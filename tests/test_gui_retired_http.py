import json
import tempfile
import threading
import unittest
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agentsassemble.gui import _make_handler


class RetiredLegacyHttpTests(unittest.TestCase):
    def test_retired_routes_return_explicit_gone_responses(self) -> None:
        routes = (
            ("GET", "/api/live-agent-create/options"),
            ("GET", "/api/provider-sessions"),
            ("GET", "/api/codex-sessions"),
            ("POST", "/api/demo"),
            ("POST", "/api/live-agent-create/check"),
            ("POST", "/api/live-agent-create"),
            ("POST", "/api/live-agent-room/expel"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(Path(temp_dir)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server_url = f"http://127.0.0.1:{server.server_port}"
                for method, path in routes:
                    with self.subTest(method=method, path=path):
                        request = Request(
                            f"{server_url}{path}",
                            data=b"{}" if method == "POST" else None,
                            headers={"Content-Type": "application/json"},
                            method=method,
                        )
                        with self.assertRaises(HTTPError) as raised:
                            urlopen(request, timeout=4)
                        error = raised.exception
                        try:
                            self.assertEqual(error.code, HTTPStatus.GONE)
                            payload = json.loads(error.read().decode("utf-8"))
                            self.assertEqual(payload["code"], "legacy_route_retired")
                            self.assertTrue(payload["details"]["replacement"])
                        finally:
                            error.close()
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
