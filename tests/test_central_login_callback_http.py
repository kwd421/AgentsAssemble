from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from agentsassemble.gui import _make_handler


def _post(url: str, payload: dict[str, object]) -> Request:
    return Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )


class CentralLoginCallbackHttpTests(unittest.TestCase):
    def test_loopback_callback_delivers_the_one_time_code_without_exposing_it_on_the_success_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(Path(temp_dir) / "room"),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                state = "state_native_callback_12345678901234567890"
                with urlopen(
                    _post(f"{base}/api/central-login/callback/start", {"state": state}),
                    timeout=4,
                ) as response:
                    started = json.loads(response.read().decode("utf-8"))
                self.assertEqual(
                    started["redirect_uri"],
                    f"{base}/api/central-login/callback",
                )

                query = urlencode(
                    {
                        "state": state,
                        "handoff_id": "goh_native_callback_0001",
                        "code": "native_callback_code_12345678901234567890",
                    }
                )
                callback_request = Request(
                    f"{base}/api/central-login/callback?{query}",
                    method="GET",
                )
                with urlopen(callback_request, timeout=4) as response:
                    self.assertEqual(response.geturl(), f"{base}/central-login-complete")
                    page = response.read().decode("utf-8")
                self.assertNotIn("native_callback_code", page)
                self.assertEqual(
                    response.headers.get_content_type(),
                    "text/html",
                )

                with urlopen(
                    _post(f"{base}/api/central-login/callback/poll", {"state": state}),
                    timeout=4,
                ) as response:
                    delivered = json.loads(response.read().decode("utf-8"))
                self.assertEqual(delivered["status"], "complete")
                self.assertEqual(delivered["handoff_id"], "goh_native_callback_0001")
                self.assertEqual(
                    delivered["authorization_code"],
                    "native_callback_code_12345678901234567890",
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_callback_rejects_proxy_provenance_even_on_a_loopback_socket(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                _make_handler(Path(temp_dir) / "room"),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                request = Request(
                    f"{base}/api/central-login/callback?"
                    + urlencode(
                        {
                            "state": "state_proxy_callback_12345678901234567890",
                            "handoff_id": "goh_proxy_callback_0002",
                            "code": "proxy_callback_code_12345678901234567890",
                        }
                    ),
                    headers={"X-Forwarded-For": "203.0.113.9"},
                    method="GET",
                )
                with self.assertRaises(HTTPError) as rejected:
                    urlopen(request, timeout=4)
                rejected.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(rejected.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
