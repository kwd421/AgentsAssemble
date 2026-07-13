from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.room_database import LEGACY_HIDDEN, VISIBLE


ROOM_STATUSES = frozenset({"active", "closed", "archived"})
PARTICIPANT_STATUSES = frozenset({"joined", "left", "kicked", "exported", "detached"})
SESSION_STATUSES = frozenset({"available", "attached", "detached", "unavailable", "error"})
ACTIVE_PARTICIPANT_STATUSES = frozenset({"joined"})


def clean_room_id(value: object) -> str:
    room_id = clean_lobby_text(value, limit=128)
    if (
        not room_id
        or room_id in {".", ".."}
        or "/" in room_id
        or "\\" in room_id
        or Path(room_id).name != room_id
    ):
        raise ValueError("room_id is required.")
    return room_id


def clean_participant_id(value: object) -> str:
    return clean_lobby_text(value, limit=128)


def clean_session_id(value: object) -> str:
    return clean_lobby_text(value, limit=128)


def clean_event_type(value: object) -> str:
    event_type = clean_lobby_text(value, limit=64)
    if not event_type:
        raise ValueError("event type is required.")
    return event_type


def safe_media_filename(value: object) -> str:
    name = Path(clean_lobby_text(value, limit=256)).name
    if name in {"", ".", ".."}:
        return ""
    return name.replace("/", "_").replace("\\", "_")


def room_status(value: object) -> str:
    status = clean_lobby_text(value, limit=32) or "active"
    if status not in ROOM_STATUSES:
        raise ValueError(f"Unsupported room status: {status}")
    return status


def participant_status(value: object) -> str:
    status = clean_lobby_text(value, limit=32) or "joined"
    if status not in PARTICIPANT_STATUSES:
        raise ValueError(f"Unsupported participant status: {status}")
    return status


def session_status(value: object) -> str:
    status = clean_lobby_text(value, limit=32) or "attached"
    if status not in SESSION_STATUSES:
        raise ValueError(f"Unsupported session status: {status}")
    return status


def build_room_record(
    room_id: str,
    *,
    label: object,
    status: object,
    existing: dict[str, object],
) -> dict[str, object]:
    now = utc_now()
    clean_status = room_status(status)
    return {
        "room_id": room_id,
        "label": clean_lobby_text(label, limit=128) or str(existing.get("label") or ""),
        "status": clean_status if existing.get("status") not in {"closed", "archived"} else existing["status"],
        "created_at": str(existing.get("created_at") or now),
        "updated_at": now,
    }


def merge_participant_record(
    room_id: str,
    participant: dict[str, object],
    existing: dict[str, object],
) -> dict[str, object]:
    participant_id = clean_participant_id(participant.get("participant_id") or participant.get("agent_id"))
    if not participant_id:
        raise ValueError("participant_id is required.")
    incoming = dict(participant)
    incoming["room_id"] = room_id
    incoming["participant_id"] = participant_id
    incoming["status"] = participant_status(incoming.get("status") or "joined")
    incoming["display_name"] = clean_lobby_text(incoming.get("display_name"), limit=64) or participant_id
    now = utc_now()
    if existing:
        return {
            **existing,
            **{key: value for key, value in incoming.items() if value not in ("", None, [], {})},
            "updated_at": now,
        }
    incoming.setdefault("created_at", now)
    incoming["updated_at"] = now
    return incoming


def update_participant_record(
    participant_id: str,
    existing: dict[str, object],
    updates: dict[str, object],
) -> dict[str, object]:
    if not existing:
        raise ValueError(f"Participant {participant_id} was not found.")
    updated = {**existing, **updates, "updated_at": utc_now()}
    if "status" in updates:
        updated["status"] = participant_status(updates.get("status"))
    return updated


def merge_session_record(
    room_id: str,
    session: dict[str, object],
    existing: dict[str, object],
) -> dict[str, object]:
    session_id = clean_session_id(session.get("session_id"))
    if not session_id:
        raise ValueError("session_id is required.")
    incoming = dict(session)
    incoming["room_id"] = room_id
    incoming["session_id"] = session_id
    incoming["status"] = session_status(incoming.get("status") or "attached")
    now = utc_now()
    if existing:
        return {
            **existing,
            **{key: value for key, value in incoming.items() if value not in ("", None, [], {})},
            "updated_at": now,
        }
    incoming.setdefault("created_at", now)
    incoming["updated_at"] = now
    return incoming


def update_session_record(
    session_id: str,
    existing: dict[str, object],
    updates: dict[str, object],
) -> dict[str, object]:
    if not existing:
        raise ValueError(f"Session {session_id} was not found.")
    updated = {**existing, **updates, "updated_at": utc_now()}
    if "status" in updates:
        updated["status"] = session_status(updates.get("status"))
    return updated


def build_room_event(
    room_id: str,
    event_type: object,
    sequence: int,
    payload: dict[str, object],
) -> tuple[dict[str, object], str, str]:
    clean_type = clean_event_type(event_type)
    clean_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"v", "id", "seq", "room_id", "type", "created_at", "actor", "visibility"}
        and value not in (None, "", [], {})
    }
    participant_id = clean_lobby_text(payload.get("participant_id") or payload.get("actor_id"), limit=128)
    participant_type = clean_lobby_text(
        payload.get("participant_type") or payload.get("actor_type"),
        limit=32,
    )
    if participant_type == "user":
        participant_type = "human"
    if participant_id and not participant_type:
        participant_type = "agent" if payload.get("participant_id") else "human"
    visibility = clean_lobby_text(payload.get("visibility"), limit=32)
    if visibility not in {VISIBLE, LEGACY_HIDDEN}:
        visibility = VISIBLE
    event: dict[str, object] = {
        "v": 1,
        "id": uuid4().hex[:12],
        "seq": int(sequence),
        "created_at": utc_now(),
        "room_id": room_id,
        "type": clean_type,
        "actor": {
            "participant_id": participant_id,
            "participant_type": participant_type,
        },
        **clean_payload,
    }
    if visibility == VISIBLE:
        event = strip_private_event_fields(event)
    else:
        event["visibility"] = visibility
    return event, visibility, participant_id


def strip_private_event_fields(value: dict[str, object]) -> dict[str, object]:
    hidden = {
        "legacy_source_path",
        "path",
        "file_path",
        "absolute_path",
        "workspace",
        "executable",
        "argv",
        "pid",
        "bridge_pid",
        "reported_provider_pid",
        "provider_session_id",
    }

    def strip(item: object) -> object:
        if isinstance(item, dict):
            return {key: strip(child) for key, child in item.items() if key not in hidden}
        if isinstance(item, list):
            return [strip(child) for child in item]
        return item

    return dict(strip(value))


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
