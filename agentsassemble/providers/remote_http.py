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
from http.client import HTTPSConnection, HTTPResponse
from ipaddress import ip_address
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request

_MAX_ERROR_BODY_BYTES = 1_048_576


class RemoteEndpointBlocked(ValueError):
    """The destination cannot safely receive remote-provider credentials."""


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
    def __init__(self, response: HTTPResponse, connection: HTTPSConnection) -> None:
        self._response = response
        self._connection = connection

    def __enter__(self) -> _ManagedResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __iter__(self):
        return iter(self._response)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()


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
            if not peer_address.is_global or str(peer_address) != address:
                raise RemoteEndpointBlocked(
                    "Remote provider connected to an address that was not approved."
                )
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


__all__ = ["RemoteEndpointBlocked", "safe_remote_urlopen"]
