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
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

from agentsassemble.admission.repository import (
    InviteSessionRepository,
)
from agentsassemble.admission.compat import InviteCompatibilityState
from agentsassemble.identity.repository import (
    LOCAL_OPERATOR_PARTICIPANT_ID,
    LOCAL_OPERATOR_USER_ID,
)
from agentsassemble.room.text import clean_room_text as clean_lobby_text
from agentsassemble.application.room_users import (
    normalize_participant_type,
    resolve_device_user,
)
from agentsassemble.admission.lan_invite import (
    NATIVE_REMOTE_ROOM_CLIENT_KIND,
    resolve_lan_invite_secret_ref,
    verify_lan_invite_token,
)
from agentsassemble.admission.invite_service import (
    READ_ONLY_INVITE_SCOPE,
    ROOM_INVITE_SCOPE,
    SESSION_TOKEN_PREFIX,
    SESSION_TOKEN_TTL_SECONDS,
    InviteApplicationService,
    create_invite_record as _create_room_invite,
    fingerprint_nonce as _nonce_fingerprint,
    invite_fingerprint as _invite_fingerprint,
    normalize_invite_client_type as _normalize_invite_client_type,
    normalize_invite_participant_type as _normalize_invite_participant_type,
    normalize_invite_scope,
    room_usage_guide as _room_usage_guide,
)
from agentsassemble.application.public_invite_runtime import (
    HOST_TOKEN_ENV,
    PUBLIC_URL_ENV,
    PublicInviteRuntime,
    normalize_public_room_url,
)
from agentsassemble.persistence.local.admission.repository import (
    JsonInviteSessionRepository,
    MemoryInviteSessionRepository,
)
from agentsassemble.admission.session_issuer import (
    RoomSessionIssuer,
    session_token_fingerprint,
)

# Compatibility facade state lives behind one explicit process-local owner.
_compatibility_state = InviteCompatibilityState()

# --- Host token gate ---
# Set AGENTSASSEMBLE_HOST_TOKEN to require auth for invite creation/management.
# Host token may be omitted only for local/LAN dev mode (no public URL).
# When AGENTSASSEMBLE_PUBLIC_URL is set, host token is required; public URL
# mode refuses host operations until a token is configured.

def get_host_token() -> str:
    """Return configured host token, or empty string if not set."""
    return _compatibility_state.public_invite_runtime.host_token()


def has_runtime_host_token() -> bool:
    """Return True when the active host token was generated for this server run."""
    return _compatibility_state.public_invite_runtime.has_runtime_host_token()


def set_runtime_host_token(token: str) -> str:
    """Set the server-lifetime host token used by GUI bootstrap flows."""
    return _compatibility_state.public_invite_runtime.set_host_token(token)


def generate_runtime_host_token() -> str:
    """Generate and store a server-lifetime host token."""
    return _compatibility_state.public_invite_runtime.generate_host_token()


def host_gate_required() -> bool:
    """Return True if host token enforcement is required.

    Host token is required when AGENTSASSEMBLE_PUBLIC_URL is set, since the
    room is reachable from the internet and must not allow unauthenticated
    host operations.
    """
    return _compatibility_state.public_invite_runtime.host_gate_required()


def verify_host_token(provided: str) -> bool:
    """Check if provided token matches the configured host token.

    If no host token is configured and no public URL is set, all requests
    are allowed (local/LAN backward-compatible mode). If a public URL is
    set but no host token is configured, all requests are rejected.
    """
    return _compatibility_state.public_invite_runtime.verify_host_token(provided)


def get_public_url() -> str:
    """Return configured public base URL for join links, or empty string."""
    return _compatibility_state.public_invite_runtime.public_url()


def set_runtime_public_url(url: str) -> str:
    """Set the server-lifetime public URL used for invite join links."""
    return _compatibility_state.public_invite_runtime.set_public_url(url)


def clear_runtime_public_url(expected_url: str = "") -> None:
    """Clear the runtime public URL, optionally only when it matches a value."""
    _compatibility_state.public_invite_runtime.clear_public_url(expected_url)


def compatibility_public_invite_runtime() -> PublicInviteRuntime:
    """Return the process-default runtime used only by compatibility callers."""

    return _compatibility_state.public_invite_runtime


def _get_invite_secret() -> str:
    """Return the repository-owned signing secret."""
    return _compatibility_state.invite_application.signing_secret()


def _session_issuer() -> RoomSessionIssuer:
    return RoomSessionIssuer(
        _compatibility_state.repository,
        token_prefix=SESSION_TOKEN_PREFIX,
        ttl_seconds=SESSION_TOKEN_TTL_SECONDS,
    )


def default_room_invite_store_path(output_root: Path) -> Path:
    """Return the local-first persistence path for public room invite state."""
    return output_root / ".agentsassemble" / "room-invite-state.json"


def configure_room_invite_store(path: str | os.PathLike[str] | None) -> None:
    """Enable or disable local persistence for invite/session state."""
    configure_room_invite_repository(
        JsonInviteSessionRepository(Path(path)) if path else MemoryInviteSessionRepository()
    )


def configure_room_invite_repository(repository: InviteSessionRepository) -> None:
    """Install the server-scoped invite/session repository."""
    _compatibility_state.configure_repository(repository)


def reload_room_invite_store() -> None:
    """Reload configured persistent state, simulating a server process restart."""
    _compatibility_state.repository.reload()


def create_room_invite(
    *,
    room_url: str,
    meeting_id: str,
    agent_id: str = "",
    display_name: str = "",
    ttl_seconds: int = 600,
    invite_scope: str = ROOM_INVITE_SCOPE,
    participant_type: str = "human",
    permission_mode: str = "",
    max_uses: int = 0,
    client_type: str = "browser",
    provider_kind: str = "manual",
    created_by_user_id: str = "",
) -> dict[str, object]:
    """Compatibility facade for the process-default invite service."""
    return _compatibility_state.invite_application.create(
        room_url=room_url,
        meeting_id=meeting_id,
        agent_id=agent_id,
        display_name=display_name,
        ttl_seconds=ttl_seconds,
        invite_scope=invite_scope,
        participant_type=participant_type,
        permission_mode=permission_mode,
        max_uses=max_uses,
        client_type=client_type,
        provider_kind=provider_kind,
        created_by_user_id=created_by_user_id,
    )


def inspect_room_invite(token: str, *, meeting_id: str = "") -> dict[str, object]:
    """Compatibility facade for side-effect-free invite inspection."""
    return _compatibility_state.invite_application.inspect(token, meeting_id=meeting_id)


def join_room_with_invite(
    token: str,
    *,
    meeting_id: str = "",
    display_name: str = "",
    device_token: str = "",
    participant_type: str = "",
    owner_display_name: str = "",
) -> dict[str, object]:
    """Verify an invite token and issue a session token for room access.

    Returns session info on success, error dict on failure.
    Single-use invites consume their nonce; reusable invites enforce max_uses.
    A device_token (client-held, stable) maps the join to one stable user, so
    re-entering keeps the same participant id/profile instead of minting a new
    guest identity each time. One identity holds one live session per room:
    older sessions for the same participant are revoked on rejoin.
    """
    secret = _get_invite_secret()

    # Check if this invite has been revoked and read its reuse policy.
    join_code = token if token.startswith("aaj1_") else ""
    join_code_fingerprint = hashlib.sha256(join_code.encode("utf-8")).hexdigest() if join_code else ""
    invite_id = _invite_fingerprint(token)
    invite_info = (
        _compatibility_state.repository.invite_for_join_code(join_code_fingerprint)
        if join_code_fingerprint
        else _compatibility_state.repository.invite(invite_id)
    )
    if join_code_fingerprint:
        invite_id = str((invite_info or {}).get("invite_id") or "")
    if invite_info and invite_info.get("revoked"):
        return {"status": "rejected", "reason": "invite_revoked"}
    invite_scope = normalize_invite_scope(invite_info.get("invite_scope") if invite_info else "")
    invite_participant_type = _normalize_invite_participant_type(
        invite_info.get("participant_type") if invite_info else "human"
    )
    invite_client_type = _normalize_invite_client_type(
        invite_info.get("client_type") if invite_info else "browser"
    )
    invite_provider_kind = clean_lobby_text(
        invite_info.get("provider_kind") if invite_info else "manual", limit=64
    ) or "manual"
    # max_uses: 1 = single-use (also the safe default for unknown/legacy
    # invites whose pending record was lost), 0 = unlimited, N > 1 = capped.
    max_uses = int(invite_info.get("max_uses", 1)) if invite_info else 1
    reusable = max_uses != 1

    if join_code:
        if invite_info is None:
            return {"status": "rejected", "reason": "invite_not_found"}
        expires_at = datetime.fromisoformat(str(invite_info.get("expires_at") or ""))
        if expires_at <= datetime.now(UTC):
            return {"status": "rejected", "reason": "token_expired"}
        if meeting_id and meeting_id != invite_info.get("meeting_id"):
            return {"status": "rejected", "reason": "meeting_mismatch"}
        claims = {
            "meeting_id": invite_info.get("meeting_id"),
            "nonce": invite_info.get("join_nonce"),
            "agent": {
                "agent_id": invite_info.get("agent_id"),
                "display_name": invite_info.get("display_name"),
            },
        }
    else:
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
    nonce_fingerprint = _nonce_fingerprint(nonce)

    consume_error = _compatibility_state.repository.consume(
        invite_id=invite_id,
        nonce_fingerprint=nonce_fingerprint,
        reusable=reusable,
        max_uses=max_uses,
    )
    if consume_error:
        return {"status": "rejected", "reason": consume_error}

    # Extract identity from verified claims
    agent_info = claims.get("agent", {}) if isinstance(claims.get("agent"), dict) else {}
    base_agent_id = str(agent_info.get("agent_id") or "")
    # Caller-declared type (browser human vs AI packet) wins over the invite's
    # default; "agent"/"ai" normalize to the roster's "remote" participant type.
    resolved_participant_type = normalize_participant_type(participant_type, default="") or invite_participant_type
    if invite_client_type == "agent_bridge":
        resolved_participant_type = "remote"
    stable_user: dict[str, object] | None = None
    if reusable:
        stable_user = resolve_device_user(
            device_token,
            display_name=display_name,
            participant_type=resolved_participant_type,
        )
        if stable_user is not None:
            # Same device → same participant id across rejoins (no ghost dupes).
            agent_id = str(stable_user["participant_id"])
        else:
            # No device identity offered: mint a unique id per join so guests
            # sharing one open link don't collide on the same roster slot.
            agent_id = f"{base_agent_id or 'guest'}-{secrets.token_hex(3)}"
    else:
        agent_id = base_agent_id
    resolved_display_name = (
        clean_lobby_text(display_name, limit=128)
        or (clean_lobby_text(stable_user.get("display_name"), limit=128) if stable_user else "")
        or str(agent_info.get("display_name") or "")
        or base_agent_id
    )
    resolved_meeting_id = str(claims.get("meeting_id") or "")
    clean_owner_display_name = clean_lobby_text(owner_display_name, limit=64)
    created_by_user_id = clean_lobby_text((invite_info or {}).get("created_by_user_id"), limit=128)

    # Session replacement is one repository operation. Do not revoke first:
    # a failed replacement must leave the currently valid token intact.
    session_token = _issue_session_token(
        agent_id=agent_id,
        display_name=resolved_display_name,
        meeting_id=resolved_meeting_id,
        invite_scope=invite_scope,
        participant_type=resolved_participant_type,
        client_type=invite_client_type,
        provider_kind=invite_provider_kind,
        owner_id=created_by_user_id,
    )

    return {
        "status": "admitted",
        "session_token": session_token,
        "agent_id": agent_id,
        "display_name": resolved_display_name,
        "meeting_id": resolved_meeting_id,
        "invite_scope": invite_scope,
        "participant_type": resolved_participant_type,
        "client_type": invite_client_type,
        "provider_kind": invite_provider_kind,
        "owner_display_name": clean_owner_display_name,
        "owner_id": created_by_user_id,
        "stable_identity": stable_user is not None,
        # The server operator's account moderates from any entrance (public
        # URL included) — the join response tells the client to unlock those
        # controls for this session.
        "operator": bool(stable_user and stable_user.get("is_operator")),
        "connection_kind": "native_cli_bridge" if invite_client_type == "agent_bridge" else NATIVE_REMOTE_ROOM_CLIENT_KIND,
        "expires_at": str(
            (_compatibility_state.repository.session(_session_fingerprint(session_token)) or {}).get("expires_at") or ""
        ),
        "guide": _room_usage_guide(
            room_url=get_public_url() or str(claims.get("room_url") or ""),
            meeting_id=resolved_meeting_id,
            agent_id=agent_id,
            display_name=resolved_display_name,
            reusable_invite=reusable,
            owner_display_name=clean_owner_display_name,
        ),
    }


def issue_paired_operator_session(
    *,
    meeting_id: str,
    display_name: str,
) -> dict[str, object]:
    """Issue a bounded room session after an operator pairing was consumed.

    This path is intentionally separate from invite admission: it never
    consumes a guest invite and always resolves to the canonical local
    operator participant. Callers must first validate and atomically consume
    an operator pairing through the identity authority.
    """
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    if not clean_meeting_id:
        raise ValueError("meeting_id is required")
    clean_display_name = (
        clean_lobby_text(display_name, limit=128) or LOCAL_OPERATOR_PARTICIPANT_ID
    )
    session_token = _issue_session_token(
        agent_id=LOCAL_OPERATOR_PARTICIPANT_ID,
        display_name=clean_display_name,
        meeting_id=clean_meeting_id,
        invite_scope=ROOM_INVITE_SCOPE,
        participant_type="human",
        client_type="browser",
        provider_kind="manual",
        owner_id=LOCAL_OPERATOR_USER_ID,
    )
    session = verify_session_token(session_token) or {}
    return {
        "status": "admitted",
        "session_token": session_token,
        "agent_id": LOCAL_OPERATOR_PARTICIPANT_ID,
        "display_name": clean_display_name,
        "meeting_id": clean_meeting_id,
        "invite_scope": ROOM_INVITE_SCOPE,
        "participant_type": "human",
        "client_type": "browser",
        "provider_kind": "manual",
        "owner_id": LOCAL_OPERATOR_USER_ID,
        "stable_identity": True,
        "operator": True,
        "connection_kind": NATIVE_REMOTE_ROOM_CLIENT_KIND,
        "expires_at": str(session.get("expires_at") or ""),
    }


def verify_session_token(token: str) -> dict[str, object] | None:
    """Verify a session token. Returns session info or None if invalid/expired."""
    return _session_issuer().verify(token)


def revoke_session(token: str) -> bool:
    """Revoke a session token (e.g., on leave). Returns True if found."""
    return _session_issuer().revoke(token)


def revoke_sessions_for_participant(meeting_id: str, participant_id: str) -> int:
    """Revoke every active session for a participant in a room (host kick).

    Session tokens are stored by fingerprint, so the host can't present the raw
    token — match on the verified agent_id (and meeting_id when given) instead.
    Returns the number of sessions removed.
    """
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    clean_participant_id = clean_lobby_text(participant_id, limit=128)
    if not clean_participant_id:
        return 0
    return _session_issuer().revoke_participant(clean_meeting_id, clean_participant_id)


def active_sessions_summary() -> list[dict[str, object]]:
    """Return safe summary of active sessions (no tokens exposed)."""
    result = []
    for session in _session_issuer().active():
        result.append({
            "agent_id": session["agent_id"],
            "display_name": session["display_name"],
            "meeting_id": session["meeting_id"],
            "invite_scope": session.get("invite_scope", ROOM_INVITE_SCOPE),
            "participant_type": _normalize_invite_participant_type(session.get("participant_type")),
            "client_type": _normalize_invite_client_type(session.get("client_type")),
            "provider_kind": clean_lobby_text(session.get("provider_kind"), limit=64),
            "joined_at": session["joined_at"],
            "expires_at": session["expires_at"],
        })
    return result


def revoke_invite(invite_id: str) -> bool:
    """Compatibility facade for revoking a pending invite."""
    return _compatibility_state.invite_application.revoke(invite_id)


def revoke_room_access(meeting_id: str) -> dict[str, int]:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    return {
        "revoked_invites": _compatibility_state.repository.revoke_room_invites(clean_meeting_id),
        "revoked_sessions": _session_issuer().revoke_room(clean_meeting_id),
    }


def pending_invites_summary() -> list[dict[str, object]]:
    """Compatibility facade for safe pending-invite summaries."""
    return _compatibility_state.invite_application.pending()


def _issue_session_token(
    *,
    agent_id: str,
    display_name: str,
    meeting_id: str,
    invite_scope: str = ROOM_INVITE_SCOPE,
    participant_type: str = "human",
    client_type: str = "browser",
    provider_kind: str = "manual",
    owner_id: str = "",
) -> str:
    """Generate and store a session token."""
    token, _session = _session_issuer().issue({
        "agent_id": agent_id,
        "display_name": display_name,
        "meeting_id": meeting_id,
        "invite_scope": normalize_invite_scope(invite_scope),
        "participant_type": _normalize_invite_participant_type(participant_type),
        "client_type": _normalize_invite_client_type(client_type),
        "provider_kind": clean_lobby_text(provider_kind, limit=64) or "manual",
        "owner_id": clean_lobby_text(owner_id, limit=128),
        "connection_kind": (
            "native_cli_bridge"
            if _normalize_invite_client_type(client_type) == "agent_bridge"
            else NATIVE_REMOTE_ROOM_CLIENT_KIND
        ),
    })
    return token


def reset_state() -> None:
    """Reset all in-memory state. For testing only."""
    _compatibility_state.reset()


def _session_fingerprint(token: str) -> str:
    return session_token_fingerprint(token)
