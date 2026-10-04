from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

from agentsassemble.application.agent_sessions.commands import merge_room_store_members
from agentsassemble.room.command_uow import RoomCommandUnitOfWork
from agentsassemble.room.text import clean_room_text as clean_lobby_text
from agentsassemble.persistence.local.identity.registry import (
    identity_store_for_output_root,
)
from agentsassemble.room.moderation import (
    is_room_member_muted,
    remove_room_member,
    set_room_member_muted,
)
from agentsassemble.room.repository import RoomRepository

ROOM_MEMBERS_FILE = "room_members.json"  # legacy JSON store; imported into identity.db once
ROOM_MEMBER_ROLES = ("human", "director", "implementer", "reviewer", "agent")
ROOM_MEMBER_ROLE_ALIASES = {
    "person": "human",
    "user": "human",
    "people": "human",
    "host": "director",
    "owner": "director",
    "lead": "director",
    "manager": "director",
    "planner": "director",
    "pm": "director",
    "coder": "implementer",
    "developer": "implementer",
    "engineer": "implementer",
    "builder": "implementer",
    "critic": "reviewer",
    "review": "reviewer",
    "qa": "reviewer",
    "auditor": "reviewer",
    "viewer": "agent",
    "watcher": "agent",
    "guest": "agent",
    "observer": "agent",
}

ROOM_MEMBER_ROLE_OPTIONS = [
    {
        "id": "human",
        "label": "사람",
        "description": "직접 참여하는 사용자나 외부 사람",
    },
    {
        "id": "director",
        "label": "진행",
        "description": "순차 대화에서 다음 참여자를 호출하고 일반 에이전트의 발언을 이어받음",
    },
    {
        "id": "implementer",
        "label": "구현",
        "description": "코드, 설정, 산출물 구현",
    },
    {
        "id": "reviewer",
        "label": "리뷰어",
        "description": "검토, 반박, 승인 전 확인",
    },
    {
        "id": "agent",
        "label": "에이전트",
        "description": "AI 세션, API, 로컬 모델, 또는 원격 룸 클라이언트",
    },
]


def normalize_room_member_role(value: object) -> str:
    normalized = _canonical_room_member_role(value)
    return normalized if normalized in ROOM_MEMBER_ROLES else "agent"


def set_canonical_room_member_role(
    repository: RoomRepository,
    *,
    meeting_id: object,
    participant_id: object,
    role: object,
) -> dict[str, object]:
    room_id = clean_lobby_text(meeting_id, limit=128)
    member_id = clean_lobby_text(participant_id, limit=128)
    requested_role = clean_lobby_text(role, limit=32)
    canonical_role = _canonical_room_member_role(requested_role)
    if not room_id:
        raise ValueError("meeting_id is required")
    if not member_id:
        raise ValueError("participant_id is required")
    if not _room_member_role_is_supported(requested_role):
        raise ValueError(f"Unsupported room member role: {role}")
    if not repository.participant(room_id, member_id):
        raise ValueError(f"Room participant does not exist: {member_id}")
    with repository.transaction(room_id) as transaction:
        member = transaction.update_participant_fields(
            member_id,
            role=canonical_role,
        )
        transaction.append_event(
            "participant_updated",
            participant_id=member_id,
            role=canonical_role,
        )
    return member


def set_canonical_room_member_role_in_unit(
    unit: RoomCommandUnitOfWork,
    *,
    participant_id: object,
    role: object,
) -> dict[str, object]:
    """Update one participant role inside the caller's canonical command UOW."""

    member_id = clean_lobby_text(participant_id, limit=128)
    requested_role = clean_lobby_text(role, limit=32)
    canonical_role = _canonical_room_member_role(requested_role)
    if not member_id:
        raise ValueError("participant_id is required")
    if not _room_member_role_is_supported(requested_role):
        raise ValueError(f"Unsupported room member role: {role}")
    if not unit.participant(member_id):
        raise ValueError(f"Room participant does not exist: {member_id}")
    member = unit.update_participant_fields(member_id, role=canonical_role)
    event = unit.append_event(
        "participant_updated",
        participant_id=member_id,
        role=canonical_role,
    )
    return {"participant": member, "event": event}


def _canonical_room_member_role(value: object) -> str:
    normalized = clean_lobby_text(value, limit=32).lower().replace("-", "_")
    normalized = ROOM_MEMBER_ROLE_ALIASES.get(normalized, normalized)
    return normalized


def _room_member_role_is_supported(value: object) -> bool:
    normalized = clean_lobby_text(value, limit=32).lower().replace("-", "_")
    return normalized in ROOM_MEMBER_ROLES or normalized in ROOM_MEMBER_ROLE_ALIASES


def read_room_members(output_root: Path, meeting_id: str = "") -> list[dict[str, object]]:
    return identity_store_for_output_root(output_root).list_memberships(meeting_id)


def upsert_room_member(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    requested_role = clean_lobby_text(payload.get("role"), limit=32)
    if requested_role and _canonical_room_member_role(requested_role) not in ROOM_MEMBER_ROLES:
        raise ValueError(f"Unsupported room member role: {payload.get('role')}")
    member = _normalize_member_record(payload)
    if not member.get("participant_id"):
        member["participant_id"] = _participant_id_from_payload(member)
    return identity_store_for_output_root(output_root).upsert_membership(member)


# Ephemeral "thinking" registry (an agent is generating a reply). It is presence,
# not persisted status — the roster recomputes status from live sessions, so a
# stored status would be overwritten. TTL auto-clears it if a resident dies
# mid-generation so the typing indicator never sticks forever.
_THINKING_TTL_SECONDS = 120.0
_thinking_lock = threading.Lock()
_thinking: dict[tuple[str, str], float] = {}


def mark_thinking(meeting_id: str, participant_id: str, on: bool, *, now: float | None = None) -> None:
    moment = time.monotonic() if now is None else now
    key = (str(meeting_id or ""), str(participant_id or ""))
    if not key[0] or not key[1]:
        return
    with _thinking_lock:
        if on:
            _thinking[key] = moment + _THINKING_TTL_SECONDS
        else:
            _thinking.pop(key, None)


def thinking_participants(meeting_id: str, *, now: float | None = None) -> set[str]:
    moment = time.monotonic() if now is None else now
    target = str(meeting_id or "")
    with _thinking_lock:
        expired = [key for key, expiry in _thinking.items() if moment > expiry]
        for key in expired:
            _thinking.pop(key, None)
        return {pid for (mid, pid), _expiry in _thinking.items() if mid == target}


def room_members_payload(
    output_root: Path,
    *,
    meeting_id: str = "",
    sessions: list[dict[str, object]] | None = None,
    repository: RoomRepository | None = None,
) -> dict[str, object]:
    room_id = clean_lobby_text(meeting_id, limit=128)
    saved_members = read_room_members(output_root, meeting_id=room_id)
    # Moderation state lives only in the saved store; live session/agent records
    # never carry it, so remember it and re-apply after merging live presence.
    muted_by_key = {
        _member_key(member): bool(member.get("muted", False)) for member in saved_members
    }
    # Mute-only records (created when muting a participant that exists purely as a
    # live agent) carry placeholder identity. Keep their mute flag above, but don't
    # let them seed a roster row or overwrite the live agent's real presence.
    by_key = {
        _member_key(member): member
        for member in saved_members
        if member.get("source") != "moderation"
    }

    live_session_keys: set[str] = set()
    for session in sessions or []:
        session_member = _member_from_session(session, meeting_id=room_id)
        if not session_member:
            continue
        key = _member_key(session_member)
        live_session_keys.add(key)
        by_key[key] = {
            **by_key.get(key, {}),
            **session_member,
        }

    for key, member in by_key.items():
        member["muted"] = muted_by_key.get(key, bool(member.get("muted", False)))
        # Invite-sourced presence is only as alive as its session token: the
        # roster row saved at join time says "online" forever, so recompute it
        # from the live session list (live agents report their own status).
        if member.get("source") in {
            "room_invite",
            "room_invite_session",
        }:
            member["status"] = "online" if key in live_session_keys else "offline"

    roster = _collapse_stale_invite_duplicates(
        list(by_key.values()), live_keys=live_session_keys
    )
    roster = merge_room_store_members(
        output_root,
        room_id,
        roster,
        repository=repository,
    )
    for member in roster:
        key = _member_key(member)
        if key in muted_by_key:
            member["muted"] = muted_by_key[key]
    members = sorted(
        roster,
        key=lambda item: (
            ROOM_MEMBER_ROLES.index(str(item.get("role") or "agent"))
            if str(item.get("role") or "agent") in ROOM_MEMBER_ROLES
            else len(ROOM_MEMBER_ROLES),
            str(item.get("display_name") or item.get("participant_id") or "").lower(),
        ),
    )
    # Ephemeral "generating a reply" flag — drives the typing indicator without
    # touching the recomputed presence status.
    thinking_ids = thinking_participants(room_id)
    for member in members:
        member["thinking"] = str(member.get("participant_id") or "") in thinking_ids
    return {
        "meeting_id": room_id,
        "members": members,
        "roles": ROOM_MEMBER_ROLE_OPTIONS,
    }


def _collapse_stale_invite_duplicates(
    members: list[dict[str, object]], *, live_keys: set[str]
) -> list[dict[str, object]]:
    """Drop ghost roster rows left by pre-stable-identity rejoins.

    Before device tokens, every rejoin minted a fresh guest participant_id, so
    one person can own many saved rows that differ only by id. Within a room,
    offline invite-sourced rows that share a display name are collapsed: they
    all vanish when a live row with that name exists, otherwise only the most
    recent one survives. Live sessions/agents are never dropped.
    """
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    passthrough: list[dict[str, object]] = []
    for member in members:
        is_stale_invite = (
            member.get("source") in {"room_invite", "room_invite_session"}
            and _member_key(member) not in live_keys
        )
        if not is_stale_invite:
            passthrough.append(member)
            continue
        name_key = str(member.get("display_name") or member.get("participant_id") or "").casefold()
        group = (str(member.get("meeting_id") or ""), name_key)
        grouped.setdefault(group, []).append(member)

    live_names = {
        (
            str(member.get("meeting_id") or ""),
            str(member.get("display_name") or member.get("participant_id") or "").casefold(),
        )
        for member in passthrough
    }
    survivors = list(passthrough)
    for group, stale_rows in grouped.items():
        if group in live_names:
            continue
        survivors.append(
            max(
                stale_rows,
                key=lambda item: str(
                    item.get("updated_at") or item.get("created_at") or ""
                ),
            )
        )
    return survivors


def _member_from_session(session: dict[str, object], *, meeting_id: str) -> dict[str, object] | None:
    participant_id = clean_lobby_text(session.get("agent_id"), limit=128)
    if not participant_id:
        return None
    session_meeting_id = clean_lobby_text(session.get("meeting_id"), limit=128)
    if meeting_id and session_meeting_id != meeting_id:
        return None
    participant_type = clean_lobby_text(session.get("participant_type") or "human", limit=32).lower()
    role = "human" if participant_type == "human" else "agent"
    return _normalize_member_record(
        {
            "meeting_id": meeting_id or session_meeting_id,
            "participant_id": participant_id,
            "display_name": session.get("display_name") or participant_id,
            "role": role,
            "participant_type": participant_type,
            "connection_kind": session.get("connection_kind") or "native_remote_room_client",
            "status": "online",
            "source": "room_invite_session",
            "last_seen_at": session.get("joined_at"),
        }
    )


def _normalize_member_record(payload: dict[str, Any]) -> dict[str, object]:
    role = normalize_room_member_role(payload.get("role"))
    display_name = clean_lobby_text(payload.get("display_name") or payload.get("name"), limit=64)
    participant_id = clean_lobby_text(
        payload.get("participant_id") or payload.get("agent_id") or payload.get("handle"),
        limit=128,
    )
    participant_type = clean_lobby_text(payload.get("participant_type") or payload.get("type"), limit=32).lower()
    if participant_type not in {"human", "subscription_ai", "api", "local", "remote", "unknown"}:
        participant_type = "unknown"
    return {
        "meeting_id": clean_lobby_text(payload.get("meeting_id"), limit=128),
        "participant_id": participant_id,
        "display_name": display_name or participant_id or "Participant",
        "role": role,
        "participant_type": participant_type,
        "provider_kind": clean_lobby_text(payload.get("provider_kind"), limit=64),
        "connection_kind": clean_lobby_text(payload.get("connection_kind"), limit=64),
        "status": clean_lobby_text(payload.get("status"), limit=32),
        "muted": bool(payload.get("muted", False)),
        "source": clean_lobby_text(payload.get("source") or "manual", limit=32),
        "created_at": clean_lobby_text(payload.get("created_at"), limit=64),
        "updated_at": clean_lobby_text(payload.get("updated_at"), limit=64),
        "last_seen_at": clean_lobby_text(payload.get("last_seen_at"), limit=64),
    }


def _participant_id_from_payload(payload: dict[str, object]) -> str:
    basis = clean_lobby_text(payload.get("display_name") or payload.get("meeting_id"), limit=96)
    slug = re.sub(r"[^A-Za-z0-9_.:-]+", "-", basis).strip("-").lower()
    return f"participant:{slug or 'manual'}"


def _member_key(member: dict[str, object]) -> str:
    meeting_id = clean_lobby_text(member.get("meeting_id"), limit=128)
    participant_id = clean_lobby_text(member.get("participant_id"), limit=128)
    return f"{meeting_id}:{participant_id}"
