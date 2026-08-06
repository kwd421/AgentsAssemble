"""Canonical principal projection for room authorization and visibility."""

from __future__ import annotations

from agentsassemble.room.text import clean_room_text


def room_identity_principals(identity: dict[str, object]) -> set[str]:
    """Return every immutable or routed id that represents one room caller."""

    principals = {
        clean_room_text(identity.get(key), limit=128)
        for key in ("principal_user_id", "user_id", "agent_id", "session_id")
    }
    principals.discard("")
    return principals


def room_identity_is_operator(identity: dict[str, object]) -> bool:
    """Prefer the immutable principal flag over legacy presentation metadata."""

    if "principal_is_operator" in identity:
        return bool(identity.get("principal_is_operator"))
    return bool(identity.get("operator"))


def room_identity_command_principal(identity: dict[str, object]) -> str:
    """Return the stable idempotency principal for a room command caller."""

    client_type = clean_room_text(identity.get("client_type"), limit=64) or "unknown"
    principal = clean_room_text(
        identity.get("principal_user_id")
        or identity.get("user_id")
        or identity.get("session_id")
        or identity.get("agent_id"),
        limit=128,
    )
    return f"{client_type}:{principal or 'anonymous'}"


__all__ = [
    "room_identity_command_principal",
    "room_identity_is_operator",
    "room_identity_principals",
]
