"""Host, origin, and public-route trust policy for the GUI server."""
from __future__ import annotations

from urllib.parse import urlparse

from agentsassemble.providers.remote_openai import remote_openai_credential_ids

_LOOPBACK_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}
_DEFAULT_PORT_BY_SCHEME = {"http": 80, "https": 443}
_PUBLIC_INVITE_CORS_METHODS = "GET, POST, DELETE, OPTIONS"
_PUBLIC_INVITE_CORS_HEADERS = "Authorization, Content-Type, Last-Event-ID, X-Device-Token"
TRUSTED_PROXY_TOKEN_HEADER = "X-AgentsAssemble-Proxy-Token"
TRUSTED_PROXY_CLIENT_IP_HEADER = "X-AgentsAssemble-Client-IP"
_PUBLIC_SERVER_IDENTITY_ROUTES = {
    ("GET", "/api/server-info"),
    ("POST", "/api/server-info/challenge"),
}
_PROVIDER_CREDENTIAL_PATHS = {
    f"/api/provider-credentials/{provider_id}"
    for provider_id in remote_openai_credential_ids()
}
_PROVIDER_CREDENTIAL_PATHS.add("/api/provider-credentials/opencode")


NormalizedOrigin = tuple[str, str, int]


def _is_loopback_host(host: object) -> bool:
    return str(host or "").strip().strip("[]").lower() in _LOOPBACK_HOSTNAMES


def _split_authority_host_port(authority: str) -> tuple[str, str]:
    authority = authority.strip()
    if authority.startswith("["):
        host, _, rest = authority[1:].partition("]")
        return host.strip().lower(), (rest[1:].strip() if rest.startswith(":") else "")
    if authority.count(":") == 1:
        host, _, port = authority.partition(":")
        return host.strip().lower(), port.strip()
    return authority.lower(), ""


def _normalized_origin(value: object) -> NormalizedOrigin | None:
    parsed = urlparse(str(value or "").strip())
    scheme = parsed.scheme.lower()
    if scheme not in _DEFAULT_PORT_BY_SCHEME:
        return None
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return None
    try:
        port = parsed.port or _DEFAULT_PORT_BY_SCHEME[scheme]
    except ValueError:
        return None
    return scheme, parsed.hostname.lower(), port


def _normalized_authority(authority: object, *, scheme: str) -> tuple[str, int] | None:
    clean_scheme = str(scheme or "").strip().lower()
    if clean_scheme not in _DEFAULT_PORT_BY_SCHEME:
        return None
    value = str(authority or "").strip()
    if not value or any(character in value for character in "/?#@"):
        return None
    parsed = urlparse(f"//{value}")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        return None
    try:
        port = parsed.port or _DEFAULT_PORT_BY_SCHEME[clean_scheme]
    except ValueError:
        return None
    return parsed.hostname.lower(), port


def _host_header_is_trusted(host_header: object, *, public_url: str = "") -> bool:
    local_authority = _normalized_authority(host_header, scheme="http")
    if local_authority and local_authority[0] in _LOOPBACK_HOSTNAMES:
        return True
    public_origin = _normalized_origin(public_url)
    if public_origin is None:
        return False
    public_scheme, public_hostname, public_port = public_origin
    return _normalized_authority(host_header, scheme=public_scheme) == (
        public_hostname,
        public_port,
    )


def _origin_is_trusted(origin: str) -> bool:
    normalized = _normalized_origin(origin)
    return normalized is not None and normalized[1] in _LOOPBACK_HOSTNAMES


def _origin_matches_request_host(origin: object, *, host_header: object) -> bool:
    normalized = _normalized_origin(origin)
    if normalized is None:
        return False
    scheme, hostname, port = normalized
    return (
        scheme == "http"
        and hostname in _LOOPBACK_HOSTNAMES
        and _normalized_authority(host_header, scheme=scheme) == (hostname, port)
    )


def _origin_matches_public_url(origin: str, *, public_url: str = "") -> bool:
    return bool(public_url) and _normalized_origin(origin) == _normalized_origin(public_url)


def _origin_is_loopback_or_empty(origin: object) -> bool:
    origin_text = str(origin or "").strip()
    return not origin_text or _origin_is_trusted(origin_text)


def _request_uses_trusted_public_https_proxy(
    *,
    peer_host: object,
    host_header: object,
    forwarded_host: object = "",
    forwarded_proto: object,
    public_url: str,
    ingress_kind: object,
    cloudflare_ray: object = "",
) -> bool:
    """Accept proxy HTTPS claims only from an authenticated ingress.

    The GUI server itself speaks HTTP. A public HTTPS request is therefore
    trustworthy only when the server has registered its managed Cloudflare
    tunnel or a user-managed proxy presents the configured shared token.
    """
    public_origin = _normalized_origin(public_url)
    clean_ingress_kind = str(ingress_kind or "").strip().lower()
    ingress_is_authenticated = clean_ingress_kind == "authenticated_proxy"
    ingress_is_managed_cloudflare = (
        clean_ingress_kind == "cloudflare" and bool(str(cloudflare_ray or "").strip())
    )
    effective_host = forwarded_host if ingress_is_managed_cloudflare else host_header
    forwarded_scheme = str(forwarded_proto or "").strip().lower()
    return (
        public_origin is not None
        and public_origin[0] == "https"
        and (ingress_is_authenticated or ingress_is_managed_cloudflare)
        and _is_loopback_host(peer_host)
        and forwarded_scheme == "https"
        and _normalized_authority(effective_host, scheme=forwarded_scheme)
        == (public_origin[1], public_origin[2])
    )


def _public_server_identity_route_allowed(path: str, method: str) -> bool:
    clean_method = str(method or "").upper()
    if clean_method == "OPTIONS":
        return path in {route_path for _, route_path in _PUBLIC_SERVER_IDENTITY_ROUTES}
    return (clean_method, path) in _PUBLIC_SERVER_IDENTITY_ROUTES


def _public_invite_route_allowed(path: str, method: str) -> bool:
    method = method.upper()
    if method == "GET":
        return (
            path
            in {
                "/join",
                "/join/",
                "/pair",
                "/pair/",
                "/ws",
                "/api",
                "/api/",
                "/api/server-info",
                "/api/room/vote",
                "/api/rooms",
                "/api/room-settings",
                "/api/room-channels",
                "/api/room/channel-lobby",
                "/api/room/voice",
                "/api/live-agent-flow",
                "/api/room-members",
                "/api/room-invite/sessions",
                "/api/room-invite/invites",
                "/api/user-profile",
                "/api/account",
                "/api/provider-usage/claude",
                "/api/provider-usage/codex",
                "/api/provider-usage/antigravity",
                "/api/provider-usage/grok",
                "/api/provider-usage/deepseek",
                "/api/provider-usage/opencode",
            }
            or path in _PROVIDER_CREDENTIAL_PATHS
            or path.startswith("/api/attachments/")
            or path.startswith("/app/assets/")
        )
    if method == "POST":
        return path in {
            "/api/server-info/challenge",
            "/api/attachments",
            "/api/ws-ticket",
            "/api/room-invite/admission",
            "/api/room-invite/agent-join",
            "/api/room-invite/join",
            "/api/operator-pairing/redeem",
            "/api/room-invite/leave",
            "/api/user-profile",
            "/api/account/google",
            "/api/account/google/challenge",
            "/api/room-invite/companion",
            "/api/room-settings",
            "/api/room-channels",
            "/api/room/channel-say",
            "/api/room/voice/join",
            "/api/room/voice/leave",
            "/api/rooms/archive",
            "/api/host/claim",
            "/api/room-members/role",
            "/api/room-members/mute",
            "/api/room-invite/create",
            "/api/room-invite/revoke",
            "/api/identity/recovery-code",
            "/api/identity/recovery-code/redeem",
        } or path in _PROVIDER_CREDENTIAL_PATHS
    if method == "DELETE":
        return path == "/api/account/google" or path in _PROVIDER_CREDENTIAL_PATHS
    if method == "OPTIONS":
        return _public_invite_route_allowed(path, "GET") or _public_invite_route_allowed(
            path,
            "POST",
        )
    return False


def _request_trusted(
    bound_host: object,
    host_header: object,
    origin: object,
    *,
    path: str = "",
    method: str = "GET",
    public_url: str = "",
) -> bool:
    # Non-loopback binding is possible only through the explicit unsafe server
    # option. Loopback remains the DNS-rebinding and CSRF boundary by default.
    if not _is_loopback_host(bound_host):
        return True
    host_trusted = _host_header_is_trusted(host_header, public_url=public_url)
    host_authority = _normalized_authority(host_header, scheme="http")
    host_is_loopback = bool(
        host_authority and host_authority[0] in _LOOPBACK_HOSTNAMES
    )
    host_is_public = host_trusted and not host_is_loopback
    if not host_trusted:
        return False
    if host_is_public and not _public_invite_route_allowed(path, method):
        return False
    # These endpoints disclose only public server identity metadata and a
    # domain-separated signature over a caller nonce. They deliberately accept
    # cross-origin probes so a trusted client shell can verify a pinned host key
    # before navigating to an otherwise untrusted endpoint.
    if host_is_public and _public_server_identity_route_allowed(path, method):
        return True
    origin_text = str(origin or "").strip()
    if not origin_text:
        return True
    if host_is_loopback:
        return _origin_matches_request_host(origin_text, host_header=host_header)
    return _origin_matches_public_url(origin_text, public_url=public_url)
