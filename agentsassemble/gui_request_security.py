"""Compatibility exports for GUI request security policy."""
from agentsassemble.web.security import (
    _LOOPBACK_HOSTNAMES,
    _PUBLIC_INVITE_CORS_HEADERS,
    _PUBLIC_INVITE_CORS_METHODS,
    _host_header_is_trusted,
    _is_loopback_host,
    _origin_is_loopback_or_empty,
    _origin_is_trusted,
    _origin_matches_public_url,
    _public_invite_route_allowed,
    _request_trusted,
    _split_authority_host_port,
)

__all__ = [
    "_LOOPBACK_HOSTNAMES",
    "_PUBLIC_INVITE_CORS_HEADERS",
    "_PUBLIC_INVITE_CORS_METHODS",
    "_host_header_is_trusted",
    "_is_loopback_host",
    "_origin_is_loopback_or_empty",
    "_origin_is_trusted",
    "_origin_matches_public_url",
    "_public_invite_route_allowed",
    "_request_trusted",
    "_split_authority_host_port",
]
