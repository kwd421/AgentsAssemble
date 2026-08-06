"""Pinned HTTPS transport for credentialed remote model providers.

Remote provider URLs are security boundaries: resolving a hostname once and
then handing it to a redirect-following client can disclose credentials to a
private address after DNS rebinding or a redirect.  This module resolves every
request, rejects any non-public answer, connects to one approved address, and
never follows redirects.
"""
from __future__ import annotations

import io
import socket
import ssl
from collections.abc import Callable
from http.client import HTTPConnection, HTTPSConnection, HTTPResponse
from ipaddress import ip_address
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request

_MAX_ERROR_BODY_BYTES = 1_048_576
MAX_REMOTE_RESPONSE_BYTES = 32 * 1_048_576
MAX_REMOTE_RESPONSE_LINE_BYTES = 8 * 1_048_576


class RemoteEndpointBlocked(ValueError):
    """The destination cannot safely receive remote-provider credentials."""


class RemoteResponseTooLarge(RuntimeError):
    """A provider exceeded the process-wide successful-response budget."""


class _PinnedHTTPSConnection(HTTPSConnection):
    def __init__(
        self,
        hostname: str,
        address: str,
        port: int,
        *,
        timeout: float,
    ) -> None:
        super().__init__(
            hostname,
            port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._pinned_address = address

    def connect(self) -> None:
        if self._tunnel_host:
            raise RemoteEndpointBlocked("Remote provider proxies are not supported.")
        raw_socket = socket.create_connection(
            (self._pinned_address, self.port),
            self.timeout,
            self.source_address,
        )
        try:
            self.sock = self._context.wrap_socket(
                raw_socket,
                server_hostname=self.host,
            )
        except Exception:
            raw_socket.close()
            raise


class _ManagedResponse:
    def __init__(
        self,
        response: HTTPResponse,
        connection: HTTPConnection,
        *,
        maximum_bytes: int = MAX_REMOTE_RESPONSE_BYTES,
        maximum_line_bytes: int = MAX_REMOTE_RESPONSE_LINE_BYTES,
    ) -> None:
        self._response = response
        self._connection = connection
        self._maximum_bytes = max(1, int(maximum_bytes))
        self._maximum_line_bytes = max(1, int(maximum_line_bytes))
        self._consumed_bytes = 0
        content_length = _content_length(response)
        if content_length is not None and content_length > self._maximum_bytes:
            self.close()
            raise RemoteResponseTooLarge("Remote provider response is too large.")

    def __enter__(self) -> _ManagedResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()

    def read(self, size: int = -1) -> bytes:
        remaining = self._maximum_bytes - self._consumed_bytes
        requested = remaining + 1 if size is None or size < 0 else min(int(size), remaining + 1)
        return self._record(self._response.read(requested))

    def read1(self, size: int = -1) -> bytes:
        return self.read(size)

    def readline(self, size: int = -1) -> bytes:
        remaining = self._maximum_bytes - self._consumed_bytes
        requested = min(remaining + 1, self._maximum_line_bytes + 1)
        if size is not None and size >= 0:
            requested = min(requested, int(size))
        line = self._response.readline(requested)
        if len(line) > self._maximum_line_bytes:
            self.close()
            raise RemoteResponseTooLarge("Remote provider response line is too large.")
        return self._record(line)

    def _record(self, data: bytes) -> bytes:
        self._consumed_bytes += len(data)
        if self._consumed_bytes > self._maximum_bytes:
            self.close()
            raise RemoteResponseTooLarge("Remote provider response is too large.")
        return data


def _content_length(response: HTTPResponse) -> int | None:
    getter = getattr(response, "getheader", None)
    raw = getter("Content-Length") if callable(getter) else None
    if raw is None:
        headers = getattr(response, "headers", {})
        raw = headers.get("Content-Length") if hasattr(headers, "get") else None
    if raw is None:
        return None
    try:
        length = int(str(raw).strip())
    except ValueError:
        return None
    return max(0, length)


def _public_addresses(
    hostname: str,
    port: int,
    *,
    resolver: Callable[..., list[tuple[object, ...]]],
) -> tuple[str, ...]:
    try:
        answers = resolver(
            hostname,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except OSError as error:
        raise URLError(error) from error
    addresses: list[str] = []
    for answer in answers:
        socket_address = answer[4] if len(answer) > 4 else ()
        candidate = str(socket_address[0] if socket_address else "").strip()
        if not candidate:
            continue
        try:
            address = ip_address(candidate)
        except ValueError as error:
            raise RemoteEndpointBlocked("Remote provider DNS returned an invalid address.") from error
        if not address.is_global:
            raise RemoteEndpointBlocked(
                "Remote provider DNS resolved to a local or private address."
            )
        normalized = str(address)
        if normalized not in addresses:
            addresses.append(normalized)
    if not addresses:
        raise URLError("Remote provider DNS returned no usable addresses.")
    return tuple(addresses)


def _loopback_addresses(
    hostname: str,
    port: int,
    *,
    resolver: Callable[..., list[tuple[object, ...]]],
) -> tuple[str, ...]:
    try:
        answers = resolver(
            hostname,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except OSError as error:
        raise URLError(error) from error
    addresses: list[str] = []
    for answer in answers:
        socket_address = answer[4] if len(answer) > 4 else ()
        candidate = str(socket_address[0] if socket_address else "").strip()
        if not candidate:
            continue
        try:
            address = ip_address(candidate)
        except ValueError as error:
            raise RemoteEndpointBlocked("Local provider DNS returned an invalid address.") from error
        if not address.is_loopback:
            raise RemoteEndpointBlocked("Local provider resolved outside loopback.")
        normalized = str(address)
        if normalized not in addresses:
            addresses.append(normalized)
    if not addresses:
        raise URLError("Local provider DNS returned no usable addresses.")
    return tuple(addresses)


def _request_target(parsed) -> str:
    return urlunsplit(("", "", parsed.path or "/", parsed.query, ""))


def safe_remote_urlopen(
    request: Request,
    timeout: float = 10.0,
    *,
    resolver: Callable[..., list[tuple[object, ...]]] = socket.getaddrinfo,
    connection_factory: Callable[..., HTTPSConnection] = _PinnedHTTPSConnection,
):
    """Open one credentialed remote HTTPS request without redirects or DNS races."""

    parsed = urlsplit(request.full_url)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise RemoteEndpointBlocked("Remote provider requests require a direct HTTPS URL.")
    if parsed.username or parsed.password or parsed.fragment:
        raise RemoteEndpointBlocked("Remote provider URL contains unsupported authority data.")
    hostname = parsed.hostname.casefold()
    port = parsed.port or 443
    addresses = _public_addresses(hostname, port, resolver=resolver)
    return _open_pinned(
        request,
        parsed=parsed,
        addresses=addresses,
        port=port,
        timeout=timeout,
        connection_factory=connection_factory,
        peer_allowed=lambda peer, approved: peer.is_global and str(peer) == approved,
        invalid_peer_message="Remote provider connected to an address that was not approved.",
    )


def safe_loopback_urlopen(
    request: Request,
    timeout: float = 10.0,
    *,
    resolver: Callable[..., list[tuple[object, ...]]] = socket.getaddrinfo,
    connection_factory: Callable[..., HTTPConnection] | None = None,
):
    """Open one explicit local-provider HTTP request without redirects or proxies."""

    parsed = urlsplit(request.full_url)
    if parsed.scheme.casefold() != "http" or not parsed.hostname:
        raise RemoteEndpointBlocked("Local provider requests require a direct loopback HTTP URL.")
    if parsed.username or parsed.password or parsed.fragment:
        raise RemoteEndpointBlocked("Local provider URL contains unsupported authority data.")
    hostname = parsed.hostname.casefold()
    port = parsed.port or 80
    addresses = _loopback_addresses(hostname, port, resolver=resolver)
    factory = connection_factory or _PinnedHTTPConnection
    return _open_pinned(
        request,
        parsed=parsed,
        addresses=addresses,
        port=port,
        timeout=timeout,
        connection_factory=factory,
        peer_allowed=lambda peer, approved: peer.is_loopback and str(peer) == approved,
        invalid_peer_message="Local provider connected outside the approved loopback address.",
    )


class _PinnedHTTPConnection(HTTPConnection):
    def __init__(
        self,
        hostname: str,
        address: str,
        port: int,
        *,
        timeout: float,
    ) -> None:
        super().__init__(hostname, port, timeout=timeout)
        self._pinned_address = address

    def connect(self) -> None:
        if self._tunnel_host:
            raise RemoteEndpointBlocked("Local provider proxies are not supported.")
        self.sock = socket.create_connection(
            (self._pinned_address, self.port),
            self.timeout,
            self.source_address,
        )


def _open_pinned(
    request: Request,
    *,
    parsed: Any,
    addresses: tuple[str, ...],
    port: int,
    timeout: float,
    connection_factory: Callable[..., HTTPConnection],
    peer_allowed: Callable[[Any, str], bool],
    invalid_peer_message: str,
):
    hostname = str(parsed.hostname).casefold()
    last_error: OSError | None = None
    for address in addresses:
        connection = connection_factory(
            hostname,
            address,
            port,
            timeout=max(1.0, float(timeout)),
        )
        try:
            connection.connect()
            peer_name = connection.sock.getpeername() if connection.sock is not None else ()
            peer_host = str(peer_name[0] if peer_name else "").strip()
            try:
                peer_address = ip_address(peer_host)
            except ValueError as error:
                raise RemoteEndpointBlocked("Remote provider peer address is invalid.") from error
            if not peer_allowed(peer_address, address):
                raise RemoteEndpointBlocked(invalid_peer_message)
            connection.request(
                request.get_method(),
                _request_target(parsed),
                body=request.data,
                headers=dict(request.header_items()),
            )
            response = connection.getresponse()
            if 200 <= int(response.status) < 300:
                return _ManagedResponse(response, connection)
            body = response.read(_MAX_ERROR_BODY_BYTES)
            response.close()
            connection.close()
            raise HTTPError(
                request.full_url,
                int(response.status),
                str(response.reason or "Remote provider request failed"),
                response.headers,
                io.BytesIO(body),
            )
        except (HTTPError, RemoteEndpointBlocked):
            connection.close()
            raise
        except OSError as error:
            last_error = error
            connection.close()
    raise URLError(last_error or "Remote provider connection failed.")


__all__ = [
    "MAX_REMOTE_RESPONSE_BYTES",
    "MAX_REMOTE_RESPONSE_LINE_BYTES",
    "RemoteEndpointBlocked",
    "RemoteResponseTooLarge",
    "safe_loopback_urlopen",
    "safe_remote_urlopen",
]
