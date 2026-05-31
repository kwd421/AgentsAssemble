"""Web-surfaced room invite: create, join, and session token management.

This module implements the authenticated remote room API that allows the
LAN invite token generation to be safely promoted from CLI-only to a web
surface. Security boundaries:

- Invite token is admission evidence only (HMAC-SHA256, short TTL, single use).
- Session token is a short-lived bearer for room read/write (not a provider
  execution grant, not a permanent approval).
- No provider CLIs are started. No secrets/endpoints/paths are exposed.
- Invite tokens are consumed on join (single-use nonce tracking).
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import secrets
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.multi_host_invites import (
    NATIVE_REMOTE_ROOM_CLIENT_KIND,
    create_lan_invite_packet,
    normalize_lan_room_url,
    resolve_lan_invite_secret_ref,
    verify_lan_invite_token,
)

# Session token config
SESSION_TOKEN_TTL_SECONDS = 3600  # 1 hour
SESSION_TOKEN_PREFIX = "aas1"  # AgentsAssemble Session v1

# In-memory state (server-lifetime scoped)
_state_lock = threading.Lock()
_active_sessions: dict[str, dict] = {}  # session_token -> session info
_used_nonces: set[str] = set()  # consumed invite nonces (replay protection)
_invite_secret: str = ""  # server-lifetime secret for invite generation


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
) -> dict[str, object]:
    """Create an invite token for a remote client to join the room.

    Called by the host from the web UI. Uses the server-lifetime secret
    so the host doesn't need to manage secrets manually.
    """
    secret = _get_invite_secret()
    clean_agent_id = clean_lobby_text(agent_id, limit=64) or f"guest-{secrets.token_hex(4)}"
    clean_display_name = clean_lobby_text(display_name, limit=128) or clean_agent_id

    packet = create_lan_invite_packet(
        room_url=room_url,
        meeting_id=meeting_id,
        agent_id=clean_agent_id,
        display_name=clean_display_name,
        provider_kind="manual",
        secret=secret,
        ttl_seconds=ttl_seconds,
    )
    return {
        "invite_token": packet["token"],
        "meeting_id": packet["meeting_id"],
        "agent_id": clean_agent_id,
        "display_name": clean_display_name,
        "expires_at": packet["expires_at"],
        "room_url": packet["room_url"],
    }


def join_room_with_invite(
    token: str,
    *,
    meeting_id: str = "",
    display_name: str = "",
) -> dict[str, object]:
    """Verify an invite token and issue a session token for room access.

    Returns session info on success, error dict on failure.
    Single-use: the invite nonce is consumed on successful join.
    """
    secret = _get_invite_secret()
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
    )

    return {
        "status": "admitted",
        "session_token": session_token,
        "agent_id": agent_id,
        "display_name": resolved_display_name,
        "meeting_id": resolved_meeting_id,
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
                "joined_at": session["joined_at"],
                "expires_at": session["expires_at"],
            })
        for key in expired_keys:
            del _active_sessions[key]
        return result


def _issue_session_token(
    *,
    agent_id: str,
    display_name: str,
    meeting_id: str,
) -> str:
    """Generate and store a session token."""
    now = datetime.now(UTC)
    raw = secrets.token_urlsafe(24)
    token = f"{SESSION_TOKEN_PREFIX}.{raw}"
    session = {
        "agent_id": agent_id,
        "display_name": display_name,
        "meeting_id": meeting_id,
        "connection_kind": NATIVE_REMOTE_ROOM_CLIENT_KIND,
        "joined_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=SESSION_TOKEN_TTL_SECONDS)).isoformat(),
    }
    with _state_lock:
        _active_sessions[token] = session
    return token


def reset_state() -> None:
    """Reset all in-memory state. For testing only."""
    global _invite_secret
    with _state_lock:
        _active_sessions.clear()
        _used_nonces.clear()
        _invite_secret = ""
