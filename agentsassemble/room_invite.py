"""Web-surfaced room invite: create, join, and session token management.

This module implements the authenticated remote room API that allows the
LAN invite token generation to be safely promoted from CLI-only to a web
surface. Security boundaries:

- Invite token is admission evidence only (HMAC-SHA256, short TTL, single use).
- Session token is a short-lived bearer for room read/write (not a provider
  execution grant, not a permanent approval).
- No provider CLIs are started. No secrets/endpoints/paths are exposed.
- Invite tokens are consumed on join (single-use nonce tracking).
- Host token gates invite creation, session listing, and invite revocation.
- Public base URL support for internet-accessible join links.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import os
import secrets
import threading
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.multi_host_invites import (
    NATIVE_REMOTE_ROOM_CLIENT_KIND,
    create_lan_invite_packet,
    resolve_lan_invite_secret_ref,
    verify_lan_invite_token,
)
from agentsassemble.remote_room_client_packet import build_remote_room_client_packet

# Session token config
SESSION_TOKEN_TTL_SECONDS = 3600  # 1 hour
SESSION_TOKEN_PREFIX = "aas1"  # AgentsAssemble Session v1

# In-memory state (server-lifetime scoped)
_state_lock = threading.Lock()
_active_sessions: dict[str, dict] = {}  # session_token -> session info
_used_nonces: set[str] = set()  # consumed invite nonces (replay protection)
_invite_secret: str = ""  # server-lifetime secret for invite generation
_pending_invites: dict[str, dict] = {}  # invite_id -> invite metadata (for revocation)
_runtime_host_token: str = ""
_runtime_public_url: str = ""

ROOM_INVITE_SCOPE = "room"
READ_ONLY_INVITE_SCOPE = "read_only"
INVITE_SCOPES = {ROOM_INVITE_SCOPE, READ_ONLY_INVITE_SCOPE}

# --- Host token gate ---
# Set AGENTSASSEMBLE_HOST_TOKEN to require auth for invite creation/management.
# Host token may be omitted only for local/LAN dev mode (no public URL).
# When AGENTSASSEMBLE_PUBLIC_URL is set, host token is required; public URL
# mode refuses host operations until a token is configured.

HOST_TOKEN_ENV = "AGENTSASSEMBLE_HOST_TOKEN"
PUBLIC_URL_ENV = "AGENTSASSEMBLE_PUBLIC_URL"


def get_host_token() -> str:
    """Return configured host token, or empty string if not set."""
    return _runtime_host_token or os.environ.get(HOST_TOKEN_ENV, "")


def set_runtime_host_token(token: str) -> str:
    """Set the server-lifetime host token used by GUI bootstrap flows."""
    global _runtime_host_token
    clean_token = str(token or "").strip()
    if not clean_token:
        raise ValueError("host token is required")
    _runtime_host_token = clean_token
    return _runtime_host_token


def generate_runtime_host_token() -> str:
    """Generate and store a server-lifetime host token."""
    return set_runtime_host_token(secrets.token_urlsafe(32))


def host_gate_required() -> bool:
    """Return True if host token enforcement is required.

    Host token is required when AGENTSASSEMBLE_PUBLIC_URL is set, since the
    room is reachable from the internet and must not allow unauthenticated
    host operations.
    """
    return bool(get_public_url())


def verify_host_token(provided: str) -> bool:
    """Check if provided token matches the configured host token.

    If no host token is configured and no public URL is set, all requests
    are allowed (local/LAN backward-compatible mode). If a public URL is
    set but no host token is configured, all requests are rejected.
    """
    expected = get_host_token()
    if not expected:
        if host_gate_required():
            return False  # public URL mode requires a host token
        return True  # local mode, no gate configured
    if not provided:
        return False
    return hmac_mod.compare_digest(expected, provided)


def get_public_url() -> str:
    """Return configured public base URL for join links, or empty string."""
    return (_runtime_public_url or os.environ.get(PUBLIC_URL_ENV) or "").rstrip("/")


def set_runtime_public_url(url: str) -> str:
    """Set the server-lifetime public URL used for invite join links."""
    global _runtime_public_url
    parsed_url = normalize_public_room_url(str(url or "").strip())
    _runtime_public_url = parsed_url.rstrip("/")
    return _runtime_public_url


def normalize_public_room_url(room_url: str) -> str:
    """Normalize an operator-supplied public room URL for join links.

    Unlike LAN invite room URLs, public invite URLs intentionally allow
    internet tunnel hosts such as ``*.trycloudflare.com``. They still reject
    userinfo, query strings, and fragments so generated join links own the
    query parameters.
    """
    value = str(room_url or "").strip().rstrip("/")
    if not value:
        raise ValueError("public invite URL is required.")
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise ValueError("public invite URL must be an HTTP(S) URL.") from None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("public invite URL must be an HTTP(S) URL.")
    try:
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        raise ValueError("public invite URL must be an HTTP(S) URL with a valid host and port.") from None
    if not hostname:
        raise ValueError("public invite URL must be an HTTP(S) URL with a valid host and port.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("public invite URL must be HTTP(S) without userinfo, query, or fragment.")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def normalize_invite_scope(value: object) -> str:
    """Return the persisted invite scope understood by room session policy."""
    scope = clean_lobby_text(value, limit=32)
    return scope if scope in INVITE_SCOPES else ROOM_INVITE_SCOPE


def clear_runtime_public_url(expected_url: str = "") -> None:
    """Clear the runtime public URL, optionally only when it matches a value."""
    global _runtime_public_url
    if expected_url and _runtime_public_url != expected_url.rstrip("/"):
        return
    _runtime_public_url = ""


def _get_invite_secret() -> str:
    """Lazily generate a server-lifetime invite secret."""
    global _invite_secret
    if not _invite_secret:
        _invite_secret = secrets.token_urlsafe(32)
    return _invite_secret


def create_room_invite(
    *,
    room_url: str,
    meeting_id: str,
    agent_id: str = "",
    display_name: str = "",
    ttl_seconds: int = 600,
    invite_scope: str = ROOM_INVITE_SCOPE,
) -> dict[str, object]:
    """Create an invite token for a remote client to join the room.

    Called by the host from the web UI. Uses the server-lifetime secret
    so the host doesn't need to manage secrets manually.

    Returns invite info including join_url when a public base URL is configured.
    """
    secret = _get_invite_secret()
    clean_agent_id = clean_lobby_text(agent_id, limit=64) or f"guest-{secrets.token_hex(4)}"
    clean_display_name = clean_lobby_text(display_name, limit=128) or clean_agent_id
    clean_invite_scope = normalize_invite_scope(invite_scope)

    packet = create_lan_invite_packet(
        room_url=room_url,
        meeting_id=meeting_id,
        agent_id=clean_agent_id,
        display_name=clean_display_name,
        provider_kind="manual",
        secret=secret,
        ttl_seconds=ttl_seconds,
    )

    nonce = str(packet.get("nonce") or "")
    invite_token = packet["token"]

    # Track pending invite for revocation (key by token fingerprint)
    invite_id = _invite_fingerprint(str(invite_token))
    with _state_lock:
        _pending_invites[invite_id] = {
            "invite_id": invite_id,
            "agent_id": clean_agent_id,
            "display_name": clean_display_name,
            "meeting_id": meeting_id,
            "invite_scope": clean_invite_scope,
            "expires_at": packet["expires_at"],
            "created_at": datetime.now(UTC).isoformat(),
            "revoked": False,
        }

    # Build join_url from public base URL if configured
    public_url = get_public_url()
    join_url = ""
    if public_url:
        join_url = f"{public_url}/join?token={invite_token}"

    result: dict[str, object] = {
        "invite_id": invite_id,
        "invite_token": invite_token,
        "meeting_id": packet["meeting_id"],
        "agent_id": clean_agent_id,
        "display_name": clean_display_name,
        "invite_scope": clean_invite_scope,
        "expires_at": packet["expires_at"],
        "room_url": packet["room_url"],
    }
    if join_url:
        result["join_url"] = join_url
    result["remote_client_packet"] = build_remote_room_client_packet(
        room_url=public_url or packet["room_url"],
        invite_token=invite_token,
        meeting_id=packet["meeting_id"],
        agent_id=clean_agent_id,
        display_name=clean_display_name,
        expires_at=packet["expires_at"],
        join_url=join_url,
    )
    return result


def _invite_fingerprint(token: str) -> str:
    """Short non-reversible fingerprint of an invite token for tracking."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def join_room_with_invite(
    token: str,
    *,
    meeting_id: str = "",
    display_name: str = "",
) -> dict[str, object]:
    """Verify an invite token and issue a session token for room access.

    Returns session info on success, error dict on failure.
    Single-use: the invite nonce is consumed on successful join.
    Revoked invites are rejected.
    """
    secret = _get_invite_secret()

    # Check if this invite has been revoked
    invite_id = _invite_fingerprint(token)
    with _state_lock:
        invite_info = _pending_invites.get(invite_id)
        if invite_info and invite_info.get("revoked"):
            return {"status": "rejected", "reason": "invite_revoked"}
        invite_scope = normalize_invite_scope(invite_info.get("invite_scope") if invite_info else "")

    verification = verify_lan_invite_token(
        token,
        secret=secret,
        expected_meeting_id=meeting_id,
    )

    if verification.get("status") != "ok":
        return {
            "status": "rejected",
            "reason": verification.get("identity_status", "verification_failed"),
        }

    claims = verification.get("claims", {})
    nonce = str(claims.get("nonce") or "")

    # Single-use check
    with _state_lock:
        if nonce in _used_nonces:
            return {"status": "rejected", "reason": "token_already_used"}
        _used_nonces.add(nonce)

    # Extract identity from verified claims
    agent_info = claims.get("agent", {}) if isinstance(claims.get("agent"), dict) else {}
    agent_id = str(agent_info.get("agent_id") or "")
    resolved_display_name = (
        clean_lobby_text(display_name, limit=128)
        or str(agent_info.get("display_name") or "")
        or agent_id
    )
    resolved_meeting_id = str(claims.get("meeting_id") or "")

    # Issue session token
    session_token = _issue_session_token(
        agent_id=agent_id,
        display_name=resolved_display_name,
        meeting_id=resolved_meeting_id,
        invite_scope=invite_scope,
    )

    return {
        "status": "admitted",
        "session_token": session_token,
        "agent_id": agent_id,
        "display_name": resolved_display_name,
        "meeting_id": resolved_meeting_id,
        "invite_scope": invite_scope,
        "connection_kind": NATIVE_REMOTE_ROOM_CLIENT_KIND,
        "expires_at": _active_sessions[session_token]["expires_at"],
    }


def verify_session_token(token: str) -> dict[str, object] | None:
    """Verify a session token. Returns session info or None if invalid/expired."""
    if not token or not token.startswith(SESSION_TOKEN_PREFIX):
        return None
    with _state_lock:
        session = _active_sessions.get(token)
        if session is None:
            return None
        expires = datetime.fromisoformat(session["expires_at"])
        if expires <= datetime.now(UTC):
            del _active_sessions[token]
            return None
        return dict(session)


def revoke_session(token: str) -> bool:
    """Revoke a session token (e.g., on leave). Returns True if found."""
    with _state_lock:
        return _active_sessions.pop(token, None) is not None


def active_sessions_summary() -> list[dict[str, object]]:
    """Return safe summary of active sessions (no tokens exposed)."""
    now = datetime.now(UTC)
    with _state_lock:
        result = []
        expired_keys = []
        for key, session in _active_sessions.items():
            expires = datetime.fromisoformat(session["expires_at"])
            if expires <= now:
                expired_keys.append(key)
                continue
            result.append({
                "agent_id": session["agent_id"],
                "display_name": session["display_name"],
                "meeting_id": session["meeting_id"],
                "invite_scope": session.get("invite_scope", ROOM_INVITE_SCOPE),
                "joined_at": session["joined_at"],
                "expires_at": session["expires_at"],
            })
        for key in expired_keys:
            del _active_sessions[key]
        return result


def revoke_invite(invite_id: str) -> bool:
    """Revoke a pending invite by its invite_id. Returns True if found."""
    with _state_lock:
        invite = _pending_invites.get(invite_id)
        if invite is None:
            return False
        invite["revoked"] = True
        return True


def pending_invites_summary() -> list[dict[str, object]]:
    """Return summary of pending (non-consumed, non-expired) invites."""
    now = datetime.now(UTC)
    with _state_lock:
        result = []
        for invite_id, info in _pending_invites.items():
            expires = datetime.fromisoformat(info["expires_at"])
            if expires <= now:
                continue
            result.append({
                "invite_id": info["invite_id"],
                "agent_id": info["agent_id"],
                "display_name": info["display_name"],
                "meeting_id": info["meeting_id"],
                "invite_scope": info.get("invite_scope", ROOM_INVITE_SCOPE),
                "expires_at": info["expires_at"],
                "created_at": info["created_at"],
                "revoked": info["revoked"],
            })
        return result


def _issue_session_token(
    *,
    agent_id: str,
    display_name: str,
    meeting_id: str,
    invite_scope: str = ROOM_INVITE_SCOPE,
) -> str:
    """Generate and store a session token."""
    now = datetime.now(UTC)
    raw = secrets.token_urlsafe(24)
    token = f"{SESSION_TOKEN_PREFIX}.{raw}"
    session = {
        "agent_id": agent_id,
        "display_name": display_name,
        "meeting_id": meeting_id,
        "invite_scope": normalize_invite_scope(invite_scope),
        "connection_kind": NATIVE_REMOTE_ROOM_CLIENT_KIND,
        "joined_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=SESSION_TOKEN_TTL_SECONDS)).isoformat(),
    }
    with _state_lock:
        _active_sessions[token] = session
    return token


def reset_state() -> None:
    """Reset all in-memory state. For testing only."""
    global _invite_secret, _runtime_host_token, _runtime_public_url
    with _state_lock:
        _active_sessions.clear()
        _used_nonces.clear()
        _pending_invites.clear()
        _invite_secret = ""
        _runtime_host_token = ""
        _runtime_public_url = ""
