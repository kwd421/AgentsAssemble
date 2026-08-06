"""Host, origin, and public-route trust policy for the GUI server."""
from __future__ import annotations

from urllib.parse import urlparse

from agentsassemble.providers.remote_openai import remote_openai_credential_ids

_LOOPBACK_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}
_PUBLIC_INVITE_CORS_METHODS = "GET, POST, DELETE, OPTIONS"
_PUBLIC_INVITE_CORS_HEADERS = "Authorization, Content-Type, Last-Event-ID, X-Device-Token"
_PROVIDER_CREDENTIAL_PATHS = {
    f"/api/provider-credentials/{provider_id}"
    for provider_id in remote_openai_credential_ids()
}


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


def _host_header_is_trusted(host_header: object, *, public_url: str = "") -> bool:
    hostname, _ = _split_authority_host_port(str(host_header or ""))
    if hostname in _LOOPBACK_HOSTNAMES:
        return True
    if not public_url:
        return False
    return hostname == (urlparse(public_url).hostname or "").lower()


def _origin_is_trusted(origin: str) -> bool:
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"}:
        return False
    hostname = (parsed.hostname or "").lower()
    return hostname in _LOOPBACK_HOSTNAMES


def _origin_matches_public_url(origin: str, *, public_url: str = "") -> bool:
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"}:
        return False
    hostname = (parsed.hostname or "").lower()
    return bool(public_url) and hostname == (urlparse(public_url).hostname or "").lower()


def _origin_is_loopback_or_empty(origin: object) -> bool:
    origin_text = str(origin or "").strip()
    return not origin_text or _origin_is_trusted(origin_text)


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
            }
            or path in _PROVIDER_CREDENTIAL_PATHS
            or path.startswith("/api/attachments/")
            or path.startswith("/app/assets/")
        )
    if method == "POST":
        return path in {
            "/api/attachments",
            "/api/ws-ticket",
            "/api/room-invite/admission",
            "/api/room-invite/agent-join",
            "/api/room-invite/join",
            "/api/operator-pairing/redeem",
            "/api/room-invite/leave",
            "/api/user-profile",
            "/api/account/google",
            "/api/account/google/handoff/start",
            "/api/account/google/handoff/configure",
            "/api/account/google/handoff/complete",
            "/api/room-invite/companion",
            "/api/room-settings",
            "/api/room-channels",
            "/api/room/channel-say",
            "/api/room/voice/join",
            "/api/room/voice/leave",
            "/api/room-members",
            "/api/rooms/archive",
            "/api/host/claim",
            "/api/room-members/role",
            "/api/room-members/mute",
            "/api/room-members/kick",
            "/api/room-invite/create",
            "/api/room-invite/revoke",
            "/api/identity/recovery-code",
            "/api/identity/recovery-code/redeem",
        } or path in _PROVIDER_CREDENTIAL_PATHS
    if method == "DELETE":
        return path in _PROVIDER_CREDENTIAL_PATHS
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
    host_name, _ = _split_authority_host_port(str(host_header or ""))
    host_is_loopback = host_name in _LOOPBACK_HOSTNAMES
    host_is_public = host_trusted and not host_is_loopback
    if not host_trusted:
        return False
    if host_is_public and not _public_invite_route_allowed(path, method):
        return False
    origin_text = str(origin or "").strip()
    if not origin_text:
        return True
    if host_is_loopback:
        return _origin_is_trusted(origin_text)
    return _origin_matches_public_url(origin_text, public_url=public_url)
