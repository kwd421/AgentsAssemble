from __future__ import annotations

import io
import unittest
from urllib.error import HTTPError
from urllib.request import Request

from agentsassemble.providers.remote_http import (
    RemoteEndpointBlocked,
    RemoteResponseTooLarge,
    safe_loopback_urlopen,
    safe_remote_urlopen,
)


class _Socket:
    def __init__(self, peer_host: str) -> None:
        self._peer_host = peer_host
        self.timeouts: list[float] = []

    def getpeername(self) -> tuple[str, int]:
        return self._peer_host, 443

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)


class _Response(io.BytesIO):
    def __init__(self, *, status: int, body: bytes = b"", headers=None) -> None:
        super().__init__(body)
        self.status = status
        self.reason = "redirect" if 300 <= status < 400 else "ok"
        self.headers = dict(headers or {})


class _TrickleResponse(_Response):
    def __init__(self, *, body: bytes, advance) -> None:
        super().__init__(status=200, body=body)
        self._advance = advance

    def read1(self, _size: int = -1) -> bytes:
        self._advance()
        return super().read(1)


class _Connection:
    def __init__(self, *, peer_host: str, response: _Response) -> None:
        self.sock = _Socket(peer_host)
        self.response = response
        self.requests: list[tuple[str, str, bytes | None, dict[str, str]]] = []
        self.closed = False

    def connect(self) -> None:
        return

    def request(self, method: str, target: str, body=None, headers=None) -> None:
        self.requests.append((method, target, body, dict(headers or {})))

    def getresponse(self) -> _Response:
        return self.response

    def close(self) -> None:
        self.closed = True


def _public_resolver(_host: str, port: int, **_kwargs):
    return [(2, 1, 6, "", ("93.184.216.34", port))]


class RemoteHttpSecurityTests(unittest.TestCase):
    def test_private_dns_answer_is_rejected_before_credentials_are_sent(self) -> None:
        connections: list[_Connection] = []

        def private_resolver(_host: str, port: int, **_kwargs):
            return [(2, 1, 6, "", ("127.0.0.1", port))]

        def connection_factory(*_args, **_kwargs):
            connection = _Connection(peer_host="127.0.0.1", response=_Response(status=200))
            connections.append(connection)
            return connection

        with self.assertRaises(RemoteEndpointBlocked):
            safe_remote_urlopen(
                Request(
                    "https://attacker.example/v1/models",
                    headers={"Authorization": "Bearer private-secret"},
                ),
                resolver=private_resolver,
                connection_factory=connection_factory,
            )

        self.assertEqual(connections, [])

    def test_non_public_connected_peer_is_rejected_before_request_write(self) -> None:
        connection = _Connection(peer_host="127.0.0.1", response=_Response(status=200))

        with self.assertRaises(RemoteEndpointBlocked):
            safe_remote_urlopen(
                Request(
                    "https://api.example.com/v1/models",
                    headers={"Authorization": "Bearer private-secret"},
                ),
                resolver=_public_resolver,
                connection_factory=lambda *_args, **_kwargs: connection,
            )

        self.assertEqual(connection.requests, [])
        self.assertTrue(connection.closed)

    def test_redirect_is_not_followed_or_given_a_second_credentialed_request(self) -> None:
        connection = _Connection(
            peer_host="93.184.216.34",
            response=_Response(
                status=302,
                headers={"Location": "http://127.0.0.1/internal"},
            ),
        )

        with self.assertRaises(HTTPError) as blocked_redirect:
            safe_remote_urlopen(
                Request(
                    "https://api.example.com/v1/models",
                    headers={"Authorization": "Bearer private-secret"},
                ),
                resolver=_public_resolver,
                connection_factory=lambda *_args, **_kwargs: connection,
            )

        self.addCleanup(blocked_redirect.exception.close)
        self.assertEqual(blocked_redirect.exception.code, 302)
        self.assertEqual(len(connection.requests), 1)
        self.assertEqual(connection.requests[0][3]["Authorization"], "Bearer private-secret")
        self.assertTrue(connection.closed)

    def test_success_body_cannot_exceed_the_shared_response_budget(self) -> None:
        response = _Response(
            status=200,
            body=b"oversized",
            headers={"Content-Length": "33554433"},
        )
        connection = _Connection(peer_host="93.184.216.34", response=response)

        with self.assertRaises(RemoteResponseTooLarge):
            safe_remote_urlopen(
                Request("https://api.example.com/v1/chat/completions"),
                resolver=_public_resolver,
                connection_factory=lambda *_args, **_kwargs: connection,
            )

        self.assertTrue(connection.closed)

    def test_streaming_line_cannot_exceed_the_shared_line_budget(self) -> None:
        response = _Response(status=200, body=b"x" * (8 * 1_048_576 + 1))
        connection = _Connection(peer_host="93.184.216.34", response=response)
        managed = safe_remote_urlopen(
            Request("https://api.example.com/v1/chat/completions"),
            resolver=_public_resolver,
            connection_factory=lambda *_args, **_kwargs: connection,
        )
        self.addCleanup(managed.close)

        with self.assertRaises(RemoteResponseTooLarge):
            managed.readline()

        self.assertTrue(connection.closed)

    def test_streaming_response_has_an_absolute_deadline_despite_trickle_bytes(self) -> None:
        now = 100.0

        def monotonic() -> float:
            return now

        def advance() -> None:
            nonlocal now
            now += 0.4

        response = _TrickleResponse(body=b"data: never finishes", advance=advance)
        connection = _Connection(peer_host="93.184.216.34", response=response)
        managed = safe_remote_urlopen(
            Request("https://api.example.com/v1/chat/completions"),
            timeout=1.0,
            resolver=_public_resolver,
            connection_factory=lambda *_args, **_kwargs: connection,
            monotonic=monotonic,
        )
        self.addCleanup(managed.close)

        with self.assertRaises(TimeoutError):
            managed.readline()

        self.assertTrue(connection.closed)

    def test_dns_and_every_http_stage_share_one_absolute_deadline(self) -> None:
        now = 100.0

        def monotonic() -> float:
            return now

        def advance(seconds: float) -> None:
            nonlocal now
            now += seconds

        def resolver(_host: str, port: int, **_kwargs):
            advance(0.4)
            return [(2, 1, 6, "", ("93.184.216.34", port))]

        class StageConnection(_Connection):
            def connect(self) -> None:
                advance(0.3)

            def request(self, method: str, target: str, body=None, headers=None) -> None:
                advance(0.2)
                super().request(method, target, body, headers)

            def getresponse(self) -> _Response:
                advance(0.2)
                return super().getresponse()

        connection = StageConnection(
            peer_host="93.184.216.34",
            response=_Response(status=200, body=b"late"),
        )
        with self.assertRaises(TimeoutError):
            safe_remote_urlopen(
                Request("https://api.example.com/v1/chat/completions"),
                timeout=1.0,
                resolver=resolver,
                connection_factory=lambda *_args, **_kwargs: connection,
                monotonic=monotonic,
            )

        self.assertTrue(connection.closed)

    def test_local_transport_rejects_a_non_loopback_dns_answer_before_write(self) -> None:
        connections: list[_Connection] = []

        def connection_factory(*_args, **_kwargs):
            connection = _Connection(peer_host="93.184.216.34", response=_Response(status=200))
            connections.append(connection)
            return connection

        with self.assertRaises(RemoteEndpointBlocked):
            safe_loopback_urlopen(
                Request(
                    "http://local-provider.example/v1/chat/completions",
                    headers={"Authorization": "Bearer local-secret"},
                ),
                resolver=_public_resolver,
                connection_factory=connection_factory,
            )

        self.assertEqual(connections, [])


if __name__ == "__main__":
    unittest.main()
