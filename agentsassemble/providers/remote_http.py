"""Pinned HTTPS transport for credentialed remote model providers.

Remote provider URLs are security boundaries: resolving a hostname once and
then handing it to a redirect-following client can disclose credentials to a
private address after DNS rebinding or a redirect.  This module resolves every
request, rejects any non-public answer, connects to one approved address, and
never follows redirects.
"""
from __future__ import annotations

import io
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from http.client import HTTPConnection, HTTPSConnection, HTTPResponse
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request

_MAX_ERROR_BODY_BYTES = 1_048_576
MAX_REMOTE_RESPONSE_BYTES = 32 * 1_048_576
MAX_REMOTE_RESPONSE_LINE_BYTES = 8 * 1_048_576
_RESPONSE_READ_CHUNK_BYTES = 64 * 1024
_MAX_CONCURRENT_RESOLUTIONS = 16
_RESOLUTION_SLOTS = threading.BoundedSemaphore(_MAX_CONCURRENT_RESOLUTIONS)
_DNS_WORKER_PATH = Path(__file__).with_name("dns_resolver_worker.py")
_MAX_DNS_WORKER_OUTPUT_BYTES = 64 * 1024


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
        absolute_deadline: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._response = response
        self._connection = connection
        self._maximum_bytes = max(1, int(maximum_bytes))
        self._maximum_line_bytes = max(1, int(maximum_line_bytes))
        self._absolute_deadline = float(absolute_deadline)
        self._monotonic = monotonic
        self._consumed_bytes = 0
        self._read_buffer = bytearray()
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
        self._assert_before_deadline()
        remaining = self._maximum_bytes - self._consumed_bytes
        requested = remaining + 1 if size is None or size < 0 else min(int(size), remaining + 1)
        if requested <= 0:
            return b""
        chunks: list[bytes] = []
        buffered = self._take_buffer(requested)
        if buffered:
            chunks.append(buffered)
        collected = len(buffered)
        while collected < requested:
            chunk = self._read_once(
                min(_RESPONSE_READ_CHUNK_BYTES, requested - collected)
            )
            if not chunk:
                break
            chunks.append(chunk)
            collected += len(chunk)
        return self._record(b"".join(chunks))

    def read1(self, size: int = -1) -> bytes:
        return self.read(size)

    def readline(self, size: int = -1) -> bytes:
        self._assert_before_deadline()
        remaining = self._maximum_bytes - self._consumed_bytes
        limit = min(remaining + 1, self._maximum_line_bytes + 1)
        if size is not None and size >= 0:
            limit = min(limit, int(size))
        if limit <= 0:
            return b""
        while True:
            newline_index = self._read_buffer.find(b"\n", 0, limit)
            if newline_index >= 0:
                line = self._take_buffer(newline_index + 1)
                break
            if len(self._read_buffer) >= limit:
                line = self._take_buffer(limit)
                break
            chunk = self._read_once(
                min(
                    _RESPONSE_READ_CHUNK_BYTES,
                    limit - len(self._read_buffer),
                )
            )
            if not chunk:
                line = self._take_buffer(min(limit, len(self._read_buffer)))
                break
            self._read_buffer.extend(chunk)
        if len(line) > self._maximum_line_bytes:
            self.close()
            raise RemoteResponseTooLarge("Remote provider response line is too large.")
        return self._record(line)

    def _take_buffer(self, size: int) -> bytes:
        selected = bytes(self._read_buffer[:size])
        del self._read_buffer[:size]
        return selected

    def _read_once(self, size: int) -> bytes:
        remaining_seconds = self._remaining_seconds()
        sock = getattr(self._connection, "sock", None)
        settimeout = getattr(sock, "settimeout", None)
        if callable(settimeout):
            settimeout(max(0.001, remaining_seconds))
        read1 = getattr(self._response, "read1", None)
        data = read1(size) if callable(read1) else self._response.read(size)
        self._assert_before_deadline()
        return data

    def _remaining_seconds(self) -> float:
        remaining = self._absolute_deadline - self._monotonic()
        if remaining <= 0:
            self.close()
            raise TimeoutError("Remote provider response exceeded its absolute deadline.")
        return remaining

    def _assert_before_deadline(self) -> None:
        self._remaining_seconds()

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
    candidates: list[str] = []
    for answer in answers:
        socket_address = answer[4] if len(answer) > 4 else ()
        candidate = str(socket_address[0] if socket_address else "").strip()
        if candidate:
            candidates.append(candidate)
    return _validated_public_addresses(candidates)


def _validated_public_addresses(candidates: list[str]) -> tuple[str, ...]:
    addresses: list[str] = []
    for candidate in candidates:
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
    candidates: list[str] = []
    for answer in answers:
        socket_address = answer[4] if len(answer) > 4 else ()
        candidate = str(socket_address[0] if socket_address else "").strip()
        if candidate:
            candidates.append(candidate)
    return _validated_loopback_addresses(candidates)


def _validated_loopback_addresses(candidates: list[str]) -> tuple[str, ...]:
    addresses: list[str] = []
    for candidate in candidates:
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
    resolver_worker_path: str | Path = _DNS_WORKER_PATH,
    connection_factory: Callable[..., HTTPSConnection] = _PinnedHTTPSConnection,
    monotonic: Callable[[], float] = time.monotonic,
):
    """Open one credentialed remote HTTPS request without redirects or DNS races."""

    parsed = urlsplit(request.full_url)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise RemoteEndpointBlocked("Remote provider requests require a direct HTTPS URL.")
    if parsed.username or parsed.password or parsed.fragment:
        raise RemoteEndpointBlocked("Remote provider URL contains unsupported authority data.")
    hostname = parsed.hostname.casefold()
    port = parsed.port or 443
    absolute_deadline = monotonic() + max(1.0, float(timeout))
    addresses = _resolve_addresses_before_deadline(
        hostname,
        port,
        resolver=resolver,
        validator=_validated_public_addresses,
        resolver_worker_path=resolver_worker_path,
        absolute_deadline=absolute_deadline,
        monotonic=monotonic,
    )
    return _open_pinned(
        request,
        parsed=parsed,
        addresses=addresses,
        port=port,
        absolute_deadline=absolute_deadline,
        connection_factory=connection_factory,
        peer_allowed=lambda peer, approved: peer.is_global and str(peer) == approved,
        invalid_peer_message="Remote provider connected to an address that was not approved.",
        monotonic=monotonic,
    )


def safe_loopback_urlopen(
    request: Request,
    timeout: float = 10.0,
    *,
    resolver: Callable[..., list[tuple[object, ...]]] = socket.getaddrinfo,
    resolver_worker_path: str | Path = _DNS_WORKER_PATH,
    connection_factory: Callable[..., HTTPConnection] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
):
    """Open one explicit local-provider HTTP request without redirects or proxies."""

    parsed = urlsplit(request.full_url)
    if parsed.scheme.casefold() != "http" or not parsed.hostname:
        raise RemoteEndpointBlocked("Local provider requests require a direct loopback HTTP URL.")
    if parsed.username or parsed.password or parsed.fragment:
        raise RemoteEndpointBlocked("Local provider URL contains unsupported authority data.")
    hostname = parsed.hostname.casefold()
    port = parsed.port or 80
    absolute_deadline = monotonic() + max(1.0, float(timeout))
    addresses = _resolve_addresses_before_deadline(
        hostname,
        port,
        resolver=resolver,
        validator=_validated_loopback_addresses,
        resolver_worker_path=resolver_worker_path,
        absolute_deadline=absolute_deadline,
        monotonic=monotonic,
    )
    factory = connection_factory or _PinnedHTTPConnection
    return _open_pinned(
        request,
        parsed=parsed,
        addresses=addresses,
        port=port,
        absolute_deadline=absolute_deadline,
        connection_factory=factory,
        peer_allowed=lambda peer, approved: peer.is_loopback and str(peer) == approved,
        invalid_peer_message="Local provider connected outside the approved loopback address.",
        monotonic=monotonic,
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
    absolute_deadline: float,
    connection_factory: Callable[..., HTTPConnection],
    peer_allowed: Callable[[Any, str], bool],
    invalid_peer_message: str,
    monotonic: Callable[[], float],
):
    hostname = str(parsed.hostname).casefold()
    last_error: OSError | None = None
    for address in addresses:
        remaining_seconds = _remaining_request_seconds(absolute_deadline, monotonic)
        connection = connection_factory(
            hostname,
            address,
            port,
            timeout=max(0.001, remaining_seconds),
        )
        try:
            connection.connect()
            _set_connection_timeout(
                connection,
                _remaining_request_seconds(absolute_deadline, monotonic),
            )
            peer_name = connection.sock.getpeername() if connection.sock is not None else ()
            peer_host = str(peer_name[0] if peer_name else "").strip()
            try:
                peer_address = ip_address(peer_host)
            except ValueError as error:
                raise RemoteEndpointBlocked("Remote provider peer address is invalid.") from error
            if not peer_allowed(peer_address, address):
                raise RemoteEndpointBlocked(invalid_peer_message)
            _set_connection_timeout(
                connection,
                _remaining_request_seconds(absolute_deadline, monotonic),
            )
            connection.request(
                request.get_method(),
                _request_target(parsed),
                body=request.data,
                headers=dict(request.header_items()),
            )
            _set_connection_timeout(
                connection,
                _remaining_request_seconds(absolute_deadline, monotonic),
            )
            response = connection.getresponse()
            _remaining_request_seconds(absolute_deadline, monotonic)
            if 200 <= int(response.status) < 300:
                return _ManagedResponse(
                    response,
                    connection,
                    absolute_deadline=absolute_deadline,
                    monotonic=monotonic,
                )
            managed_error_response = _ManagedResponse(
                response,
                connection,
                absolute_deadline=absolute_deadline,
                monotonic=monotonic,
            )
            body = managed_error_response.read(_MAX_ERROR_BODY_BYTES)
            managed_error_response.close()
            raise HTTPError(
                request.full_url,
                int(response.status),
                str(response.reason or "Remote provider request failed"),
                response.headers,
                io.BytesIO(body),
            )
        except (HTTPError, RemoteEndpointBlocked, TimeoutError):
            connection.close()
            raise
        except OSError as error:
            last_error = error
            connection.close()
    raise URLError(last_error or "Remote provider connection failed.")


def _resolve_addresses_before_deadline(
    hostname: str,
    port: int,
    *,
    resolver: Callable[..., list[tuple[object, ...]]],
    validator: Callable[[list[str]], tuple[str, ...]],
    resolver_worker_path: str | Path,
    absolute_deadline: float,
    monotonic: Callable[[], float],
) -> tuple[str, ...]:
    if resolver is not socket.getaddrinfo:
        if validator is _validated_public_addresses:
            result = _public_addresses(hostname, port, resolver=resolver)
        else:
            result = _loopback_addresses(hostname, port, resolver=resolver)
        _remaining_request_seconds(absolute_deadline, monotonic)
        return result

    candidates = _run_system_dns_worker(
        hostname,
        port,
        worker_path=resolver_worker_path,
        absolute_deadline=absolute_deadline,
        monotonic=monotonic,
    )
    return validator(candidates)


def _run_system_dns_worker(
    hostname: str,
    port: int,
    *,
    worker_path: str | Path,
    absolute_deadline: float,
    monotonic: Callable[[], float],
) -> list[str]:
    """Resolve in a killable helper so timeout always returns its capacity slot."""

    if not _RESOLUTION_SLOTS.acquire(blocking=False):
        raise TimeoutError("Remote provider DNS resolver capacity is exhausted.")
    process: subprocess.Popen[bytes] | None = None
    try:
        environment = {"PATH": os.defpath, "PYTHONIOENCODING": "utf-8"}
        for name in ("SYSTEMROOT", "WINDIR"):
            if os.environ.get(name):
                environment[name] = os.environ[name]
        process = subprocess.Popen(
            _dns_worker_command(hostname, port, worker_path=worker_path),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
        try:
            stdout, _stderr = process.communicate(
                timeout=_remaining_request_seconds(absolute_deadline, monotonic)
            )
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.communicate()
            raise TimeoutError(
                "Remote provider DNS exceeded its absolute deadline."
            ) from error
        _remaining_request_seconds(absolute_deadline, monotonic)
        if process.returncode != 0:
            raise URLError("Remote provider DNS resolution failed.")
        if len(stdout) > _MAX_DNS_WORKER_OUTPUT_BYTES:
            raise RemoteEndpointBlocked("Remote provider DNS returned too many addresses.")
        try:
            payload = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise URLError("Remote provider DNS returned an invalid response.") from error
        if not isinstance(payload, list) or not all(
            isinstance(item, str) for item in payload
        ):
            raise URLError("Remote provider DNS returned an invalid response.")
        return payload
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        _RESOLUTION_SLOTS.release()


def _dns_worker_command(
    hostname: str,
    port: int,
    *,
    worker_path: str | Path,
) -> list[str]:
    if getattr(sys, "frozen", False) and Path(worker_path) == _DNS_WORKER_PATH:
        return [
            sys.executable,
            "--internal-dns-resolver",
            hostname,
            str(port),
        ]
    return [
        sys.executable,
        "-I",
        str(worker_path),
        hostname,
        str(port),
    ]


def _remaining_request_seconds(
    absolute_deadline: float,
    monotonic: Callable[[], float],
) -> float:
    remaining = absolute_deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("Remote provider request exceeded its absolute deadline.")
    return max(0.001, remaining)


def _set_connection_timeout(connection: HTTPConnection, timeout: float) -> None:
    sock = getattr(connection, "sock", None)
    settimeout = getattr(sock, "settimeout", None)
    if callable(settimeout):
        settimeout(timeout)


__all__ = [
    "MAX_REMOTE_RESPONSE_BYTES",
    "MAX_REMOTE_RESPONSE_LINE_BYTES",
    "RemoteEndpointBlocked",
    "RemoteResponseTooLarge",
    "safe_loopback_urlopen",
    "safe_remote_urlopen",
]
