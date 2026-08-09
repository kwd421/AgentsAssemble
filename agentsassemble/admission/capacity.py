"""Shared durable capacity policy for room access sessions."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from agentsassemble.room.text import clean_room_text


MAX_ACTIVE_ROOM_SESSIONS = 512
MAX_PUBLIC_ROOM_SESSIONS = 448
MAX_ACTIVE_SESSIONS_PER_ROOM = 128
MAX_PUBLIC_SESSIONS_PER_ROOM = 112
MAX_REUSABLE_INVITE_PRINCIPALS = 128


class RoomSessionCapacityExceeded(RuntimeError):
    """A bounded room-access session pool cannot accept another principal."""


def effective_invite_use_limit(max_uses: object) -> int:
    """Return the durable principal ceiling for one invite.

    A configured zero still means reusable, but it cannot mint an unbounded
    number of durable identities. Explicit limits are also capped at the same
    server safety ceiling.
    """

    configured = max(0, int(max_uses or 0))
    if configured == 1:
        return 1
    return min(configured or MAX_REUSABLE_INVITE_PRINCIPALS, MAX_REUSABLE_INVITE_PRINCIPALS)


def enforce_room_session_capacity(
    existing_records: Iterable[dict[str, object]],
    candidate: dict[str, object],
    *,
    now: datetime | None = None,
) -> None:
    """Reject a new durable session when its shared capacity is exhausted.

    Replacing the same room participant does not consume another slot. Agent
    Bridge and operator sessions may use the reserved tail of each pool, so a
    public browser cannot prevent room recovery or operator access.
    """

    moment = now or datetime.now(UTC)
    room_id = clean_room_text(candidate.get("meeting_id"), limit=128)
    participant_id = clean_room_text(candidate.get("agent_id"), limit=128)
    privileged = _is_reserved_subject(candidate)
    active: list[dict[str, object]] = []
    for record in existing_records:
        if not _is_active(record, moment):
            continue
        if (
            clean_room_text(record.get("meeting_id"), limit=128) == room_id
            and clean_room_text(record.get("agent_id"), limit=128) == participant_id
        ):
            continue
        active.append(record)

    room_active = [
        record
        for record in active
        if clean_room_text(record.get("meeting_id"), limit=128) == room_id
    ]
    if len(active) >= MAX_ACTIVE_ROOM_SESSIONS:
        raise RoomSessionCapacityExceeded("room session capacity reached")
    if len(room_active) >= MAX_ACTIVE_SESSIONS_PER_ROOM:
        raise RoomSessionCapacityExceeded("room participant capacity reached")
    if privileged:
        return
    if sum(not _is_reserved_subject(record) for record in active) >= MAX_PUBLIC_ROOM_SESSIONS:
        raise RoomSessionCapacityExceeded("public room session capacity reached")
    if (
        sum(not _is_reserved_subject(record) for record in room_active)
        >= MAX_PUBLIC_SESSIONS_PER_ROOM
    ):
        raise RoomSessionCapacityExceeded("public room participant capacity reached")


def _is_reserved_subject(record: dict[str, object]) -> bool:
    return bool(record.get("principal_is_operator")) or clean_room_text(
        record.get("client_type"),
        limit=32,
    ) == "agent_bridge"


def _is_active(record: dict[str, object], now: datetime) -> bool:
    try:
        expiry = datetime.fromisoformat(str(record.get("expires_at") or ""))
    except ValueError:
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    return expiry > now


__all__ = [
    "MAX_ACTIVE_ROOM_SESSIONS",
    "MAX_ACTIVE_SESSIONS_PER_ROOM",
    "MAX_PUBLIC_ROOM_SESSIONS",
    "MAX_PUBLIC_SESSIONS_PER_ROOM",
    "MAX_REUSABLE_INVITE_PRINCIPALS",
    "RoomSessionCapacityExceeded",
    "effective_invite_use_limit",
    "enforce_room_session_capacity",
]
