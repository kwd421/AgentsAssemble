import unittest

from agentsassemble.web.security import (
    _request_trusted,
    _request_uses_trusted_public_https_proxy,
)


class WebRequestOriginIsolationTests(unittest.TestCase):
    def test_loopback_browser_origin_must_match_exact_request_origin(self) -> None:
        request = {
            "bound_host": "127.0.0.1",
            "host_header": "127.0.0.1:8765",
            "path": "/api/rooms",
            "method": "GET",
        }

        self.assertTrue(_request_trusted(origin="http://127.0.0.1:8765", **request))
        self.assertFalse(_request_trusted(origin="http://127.0.0.1:9999", **request))
        self.assertFalse(_request_trusted(origin="http://localhost:8765", **request))
        self.assertFalse(_request_trusted(origin="https://127.0.0.1:8765", **request))

    def test_public_browser_origin_must_match_configured_public_origin(self) -> None:
        request = {
            "bound_host": "127.0.0.1",
            "host_header": "rooms.example.com:8443",
            "path": "/api/rooms",
            "method": "GET",
            "public_url": "https://rooms.example.com:8443",
        }

        self.assertTrue(
            _request_trusted(origin="https://rooms.example.com:8443", **request)
        )
        self.assertFalse(
            _request_trusted(origin="https://rooms.example.com", **request)
        )
        self.assertFalse(
            _request_trusted(origin="http://rooms.example.com:8443", **request)
        )
        self.assertFalse(
            _request_trusted(
                origin="https://rooms.example.com:8443",
                **{**request, "host_header": "rooms.example.com"},
            )
        )

    def test_public_google_disconnect_is_an_allowed_account_mutation(self) -> None:
        self.assertTrue(
            _request_trusted(
                bound_host="127.0.0.1",
                host_header="rooms.example.com",
                origin="https://rooms.example.com",
                path="/api/account/google",
                method="DELETE",
                public_url="https://rooms.example.com",
            )
        )

    def test_native_clients_without_origin_keep_trusted_host_access(self) -> None:
        self.assertTrue(
            _request_trusted(
                "127.0.0.1",
                "127.0.0.1:8765",
                "",
                path="/api/rooms",
                method="GET",
            )
        )

    def test_authenticated_proxy_requires_exact_public_authority(self) -> None:
        request = {
            "peer_host": "127.0.0.1",
            "host_header": "rooms.example.com:8443",
            "forwarded_proto": "https",
            "public_url": "https://rooms.example.com:8443",
            "ingress_kind": "authenticated_proxy",
        }

        self.assertTrue(_request_uses_trusted_public_https_proxy(**request))
        self.assertFalse(
            _request_uses_trusted_public_https_proxy(
                **{**request, "host_header": "rooms.example.com"}
            )
        )
        self.assertFalse(
            _request_uses_trusted_public_https_proxy(
                **{**request, "forwarded_proto": "http"}
            )
        )

    def test_managed_cloudflare_proxy_uses_exact_forwarded_authority(self) -> None:
        request = {
            "peer_host": "::1",
            "host_header": "127.0.0.1:8765",
            "forwarded_host": "rooms.example.com",
            "forwarded_proto": "https",
            "public_url": "https://rooms.example.com",
            "ingress_kind": "cloudflare",
            "cloudflare_ray": "abc123",
        }

        self.assertTrue(_request_uses_trusted_public_https_proxy(**request))
        self.assertFalse(
            _request_uses_trusted_public_https_proxy(
                **{**request, "forwarded_host": "rooms.example.com:8443"}
            )
        )


if __name__ == "__main__":
    unittest.main()
