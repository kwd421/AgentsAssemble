"""Canonical room command and projection adapters used by Agent Sessions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterator

from agentsassemble.room.repository import RoomRepository
from agentsassemble.room.text import clean_room_text


def room_sse_frames_after_cursor(
    output_root: Path,
    room_id: str,
    *,
    cursor: str = "",
    include_heartbeat: bool = True,
    repository: RoomRepository,
) -> list[str]:
    del output_root
    events = repository.read_events(room_id, after=cursor)
    if not events:
        return ["event: heartbeat\ndata: {}\n\n"] if include_heartbeat else []
    frames = []
    for event in events:
        event_type = str(event.get("type") or "message")
        event_id = str(event.get("id") or "")
        lines = []
        if event_id:
            lines.append(f"id: {event_id}")
        lines.append(f"event: {event_type}")
        lines.append(f"data: {json.dumps(event, ensure_ascii=False, sort_keys=True)}")
        frames.append("\n".join(lines) + "\n\n")
    return frames


def stream_room_sse_frames(
    output_root: Path,
    room_id: str,
    *,
    cursor: str = "",
    max_iterations: int | None = None,
    wait: Callable[[], None] | None = None,
    repository: RoomRepository,
) -> Iterator[str]:
    current_cursor = cursor
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        frames = room_sse_frames_after_cursor(
            output_root,
            room_id,
            cursor=current_cursor,
            repository=repository,
        )
        for frame in frames:
            event_id = _sse_frame_id(frame)
            if event_id:
                current_cursor = event_id
            yield frame
        iterations += 1
        if wait is not None:
            wait()


def room_status_payload(
    output_root: Path,
    room_id: str,
    *,
    repository: RoomRepository,
) -> dict[str, object]:
    del output_root
    payload = repository.room_payload(room_id)
    payload["active_participants"] = repository.active_participants(room_id)
    return payload


def room_action_payload(
    output_root: Path,
    payload: dict[str, object],
    action: str,
    *,
    repository: RoomRepository,
) -> dict[str, object]:
    room_id = clean_room_text(payload.get("room_id") or payload.get("meeting_id"), limit=128)
    participant_id = clean_room_text(payload.get("participant_id") or payload.get("agent_id"), limit=128)
    reason = clean_room_text(payload.get("reason"), limit=500)
    if action == "leave":
        participant = repository.set_participant_status(room_id, participant_id, "left", reason=reason)
        return {
            "status": "left",
            "participant": participant,
            **room_status_payload(output_root, room_id, repository=repository),
        }
    if action == "kick":
        participant = repository.set_participant_status(room_id, participant_id, "kicked", reason=reason)
        return {
            "status": "kicked",
            "participant": participant,
            **room_status_payload(output_root, room_id, repository=repository),
        }
    if action == "export":
        result = repository.export_participant(room_id, participant_id, reason=reason)
        return {
            "status": "exported",
            **result,
            **room_status_payload(output_root, room_id, repository=repository),
        }
    raise ValueError(f"Unsupported room action: {action}")


def room_lifecycle_payload(
    output_root: Path,
    payload: dict[str, object],
    action: str,
    *,
    repository: RoomRepository,
) -> dict[str, object]:
    room_id = clean_room_text(payload.get("room_id") or payload.get("meeting_id"), limit=128)
    status = "archived" if action == "archive" else "closed"
    room = repository.set_room_status(room_id, status)
    return {
        "status": status,
        "room": room,
        **room_status_payload(output_root, room_id, repository=repository),
    }


def active_room_members(
    output_root: Path,
    room_id: str,
    *,
    repository: RoomRepository,
) -> list[dict[str, object]]:
    del output_root
    return repository.active_participants(room_id)


def merge_room_store_members(
    output_root: Path,
    meeting_id: str,
    existing_members: list[dict[str, object]],
    *,
    repository: RoomRepository,
) -> list[dict[str, object]]:
    del output_root
    if not meeting_id:
        return existing_members
    participants = repository.participants(meeting_id)
    active = [participant for participant in participants if str(participant.get("status") or "") == "joined"]
    by_id: dict[str, dict[str, object]] = {}
    for participant in active:
        participant_id = str(participant.get("participant_id") or "")
        session = repository.session(meeting_id, str(participant.get("session_id") or participant_id))
        existing = next(
            (
                member
                for member in existing_members
                if str(member.get("participant_id") or "") == participant_id
            ),
            {},
        )
        by_id[participant_id] = {
            "meeting_id": meeting_id,
            "participant_id": participant.get("participant_id", ""),
            "display_name": participant.get("display_name", ""),
            "role": participant.get("role", "agent"),
            "participant_type": participant.get("participant_type", "local"),
            "provider_kind": participant.get("provider_kind", ""),
            "connection_kind": "agent_session",
            "status": existing.get("status") or participant.get("status", ""),
            "session_id": participant.get("session_id", ""),
            "owner_id": participant.get("owner_id", ""),
            "created_by": participant.get("created_by", ""),
            "model_id": participant.get("model", ""),
            "effort": participant.get("effort", ""),
            "sandbox_enforcement": participant.get("sandbox", ""),
            "permission_option": participant.get("permissions", ""),
            "runtime_sharing_policy": participant.get("runtime_sharing_policy", ""),
            "execution_mode": "agent_session_app_server",
            "engagement_mode": "agent_session",
            "join_semantics": "agent_session",
            "session_status": session.get("status", ""),
            "source": "agent_session",
            "muted": bool(existing.get("muted", False)),
            "created_at": participant.get("created_at", ""),
            "updated_at": participant.get("updated_at", ""),
            "last_seen_at": participant.get("updated_at", ""),
        }
    return list(by_id.values())


def clean_room_request_payload(value: Any) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _sse_frame_id(frame: str) -> str:
    for line in frame.splitlines():
        if line.startswith("id:"):
            return line.removeprefix("id:").strip()
    return ""
