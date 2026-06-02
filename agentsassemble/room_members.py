from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.room_friends import room_friend_type_for_agent

ROOM_MEMBERS_FILE = "room_members.json"
ROOM_MEMBER_ROLES = ("human", "director", "implementer", "reviewer", "agent")

ROOM_MEMBER_ROLE_OPTIONS = [
    {
        "id": "human",
        "label": "사람",
        "description": "직접 참여하는 사용자나 외부 사람",
    },
    {
        "id": "director",
        "label": "디렉터",
        "description": "방향 결정과 최종 승인",
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


def _canonical_room_member_role(value: object) -> str:
    normalized = clean_lobby_text(value, limit=32).lower().replace("-", "_")
    aliases = {
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
    normalized = aliases.get(normalized, normalized)
    return normalized


def read_room_members(output_root: Path, meeting_id: str = "") -> list[dict[str, object]]:
    path = output_root / ROOM_MEMBERS_FILE
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    members = payload.get("members")
    if not isinstance(members, list):
        return []
    normalized = [_normalize_member_record(member) for member in members if isinstance(member, dict)]
    room_id = clean_lobby_text(meeting_id, limit=128)
    return [
        member
        for member in normalized
        if member.get("participant_id")
        and (not room_id or member.get("meeting_id") == room_id)
    ]


def upsert_room_member(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    output_root.mkdir(parents=True, exist_ok=True)
    requested_role = clean_lobby_text(payload.get("role"), limit=32)
    if requested_role and _canonical_room_member_role(requested_role) not in ROOM_MEMBER_ROLES:
        raise ValueError(f"Unsupported room member role: {payload.get('role')}")
    member = _normalize_member_record(payload)
    participant_id = clean_lobby_text(member.get("participant_id"), limit=128) or _participant_id_from_payload(member)
    if member["role"] not in ROOM_MEMBER_ROLES:
        raise ValueError(f"Unsupported room member role: {member['role']}")
    now = datetime.now(UTC).isoformat()
    member["participant_id"] = participant_id
    if not member.get("created_at"):
        member["created_at"] = now
    member["updated_at"] = now

    members = read_room_members(output_root)
    member_key = _member_key(member)
    by_key = {_member_key(existing): existing for existing in members}
    if member_key in by_key:
        existing = dict(by_key[member_key])
        existing.update({key: value for key, value in member.items() if value not in ("", [], {})})
        existing["updated_at"] = now
        member = existing
    by_key[member_key] = member
    ordered = sorted(by_key.values(), key=lambda item: str(item.get("updated_at", "")), reverse=True)
    _write_room_members(output_root, ordered)
    return member


def room_members_payload(
    output_root: Path,
    agents: list[dict[str, object]],
    *,
    meeting_id: str = "",
) -> dict[str, object]:
    room_id = clean_lobby_text(meeting_id, limit=128)
    saved_members = read_room_members(output_root, meeting_id=room_id)
    by_key = {_member_key(member): member for member in saved_members}

    for agent in agents:
        agent_member = _member_from_agent(agent, meeting_id=room_id)
        if not agent_member:
            continue
        key = _member_key(agent_member)
        if key in by_key:
            merged = dict(agent_member)
            merged.update(by_key[key])
            by_key[key] = merged
        else:
            by_key[key] = agent_member

    members = sorted(
        by_key.values(),
        key=lambda item: (
            ROOM_MEMBER_ROLES.index(str(item.get("role") or "agent"))
            if str(item.get("role") or "agent") in ROOM_MEMBER_ROLES
            else len(ROOM_MEMBER_ROLES),
            str(item.get("display_name") or item.get("participant_id") or "").lower(),
        ),
    )
    return {
        "meeting_id": room_id,
        "members": members,
        "roles": ROOM_MEMBER_ROLE_OPTIONS,
    }


def _member_from_agent(agent: dict[str, object], *, meeting_id: str) -> dict[str, object] | None:
    agent_id = clean_lobby_text(agent.get("agent_id"), limit=128)
    if not agent_id:
        return None
    agent_meeting_id = clean_lobby_text(agent.get("meeting_id"), limit=128)
    if meeting_id and agent_meeting_id != meeting_id:
        return None
    participant_type = room_friend_type_for_agent(agent)
    return _normalize_member_record(
        {
            "meeting_id": meeting_id or agent_meeting_id,
            "participant_id": agent_id,
            "display_name": agent.get("display_name") or agent_id,
            "role": _default_role_for_agent(agent, participant_type),
            "participant_type": participant_type,
            "provider_kind": agent.get("provider_kind"),
            "connection_kind": agent.get("connection_kind"),
            "status": agent.get("status"),
            "source": "live_agent",
            "last_seen_at": agent.get("last_seen_at"),
        }
    )


def _default_role_for_agent(agent: dict[str, object], participant_type: str) -> str:
    if participant_type == "human":
        return "human"
    text = " ".join(
        clean_lobby_text(agent.get(key), limit=128).lower()
        for key in ("agent_id", "display_name", "provider_kind", "connection_kind", "engagement_mode")
    )
    if any(token in text for token in ("director", "lead", "manager", "owner", "pm", "planner", "디렉터", "팀장")):
        return "director"
    if any(token in text for token in ("review", "reviewer", "critic", "qa", "audit", "리뷰", "검토")):
        return "reviewer"
    if any(token in text for token in ("impl", "implement", "coder", "engineer", "builder", "dev", "구현", "개발")):
        return "implementer"
    return "agent"


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


def _write_room_members(output_root: Path, members: list[dict[str, object]]) -> None:
    path = output_root / ROOM_MEMBERS_FILE
    path.write_text(
        json.dumps({"members": members}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
