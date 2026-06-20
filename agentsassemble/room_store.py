from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agentsassemble.meeting_events import clean_lobby_text

ROOM_STATUSES = {"active", "closed", "archived"}
PARTICIPANT_STATUSES = {"joined", "left", "kicked", "exported", "detached"}
SESSION_STATUSES = {"available", "attached", "detached", "unavailable", "error"}
ACTIVE_PARTICIPANT_STATUSES = {"joined"}


class RoomStore:
    """Small file-backed source of truth for active room/session state."""

    def __init__(self, output_root: Path) -> None:
        self.output_root = Path(output_root)
        self.rooms_root = self.output_root / "rooms"
        self._lock = threading.RLock()

    def create_room(self, room_id: str, *, label: str = "", status: str = "active") -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        clean_status = _room_status(status)
        with self._lock:
            existing = self.room(clean_room_id)
            now = _now()
            room = {
                "room_id": clean_room_id,
                "label": clean_lobby_text(label, limit=128) or str(existing.get("label") or ""),
                "status": clean_status if existing.get("status") not in {"closed", "archived"} else existing["status"],
                "created_at": str(existing.get("created_at") or now),
                "updated_at": now,
            }
            self._room_dir(clean_room_id).mkdir(parents=True, exist_ok=True)
            self._write_json(self._room_path(clean_room_id), room)
            if not existing:
                self.append_event(clean_room_id, "room_created", label=room["label"])
            return room

    def room(self, room_id: str) -> dict[str, object]:
        path = self._room_path(_clean_room_id(room_id))
        if not path.exists():
            return {}
        return _read_json_object(path)

    def list_rooms(self, *, include_archived: bool = False) -> list[dict[str, object]]:
        if not self.rooms_root.exists():
            return []
        rooms = []
        for room_json in self.rooms_root.glob("*/room.json"):
            room = _read_json_object(room_json)
            if not room:
                continue
            if not include_archived and room.get("status") == "archived":
                continue
            rooms.append(room)
        return sorted(rooms, key=lambda room: str(room.get("updated_at") or ""), reverse=True)

    def participants(self, room_id: str) -> list[dict[str, object]]:
        return _read_json_list(self._participants_path(_clean_room_id(room_id)), "participants")

    def participant(self, room_id: str, participant_id: str) -> dict[str, object]:
        clean_participant_id = _clean_participant_id(participant_id)
        for participant in self.participants(room_id):
            if participant.get("participant_id") == clean_participant_id:
                return participant
        return {}

    def active_participants(self, room_id: str) -> list[dict[str, object]]:
        return [
            participant
            for participant in self.participants(room_id)
            if str(participant.get("status") or "") in ACTIVE_PARTICIPANT_STATUSES
        ]

    def upsert_participant(self, room_id: str, participant: dict[str, object]) -> tuple[dict[str, object], bool]:
        clean_room_id = _clean_room_id(room_id)
        clean_participant_id = _clean_participant_id(participant.get("participant_id") or participant.get("agent_id"))
        if not clean_participant_id:
            raise ValueError("participant_id is required.")
        incoming = dict(participant)
        incoming["room_id"] = clean_room_id
        incoming["participant_id"] = clean_participant_id
        incoming["status"] = _participant_status(incoming.get("status") or "joined")
        incoming["display_name"] = clean_lobby_text(incoming.get("display_name"), limit=64) or clean_participant_id
        now = _now()
        with self._lock:
            participants = self.participants(clean_room_id)
            created = True
            for index, existing in enumerate(participants):
                if existing.get("participant_id") != clean_participant_id:
                    continue
                created = False
                merged = {
                    **existing,
                    **{key: value for key, value in incoming.items() if value not in ("", None, [], {})},
                    "updated_at": now,
                }
                participants[index] = merged
                self._write_json(self._participants_path(clean_room_id), {"participants": participants})
                return merged, created
            incoming.setdefault("created_at", now)
            incoming["updated_at"] = now
            participants.append(incoming)
            self._write_json(self._participants_path(clean_room_id), {"participants": participants})
            return incoming, created

    def set_participant_status(
        self,
        room_id: str,
        participant_id: str,
        status: str,
        *,
        reason: str = "",
    ) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        clean_participant_id = _clean_participant_id(participant_id)
        clean_status = _participant_status(status)
        with self._lock:
            participants = self.participants(clean_room_id)
            updated: dict[str, object] | None = None
            for index, participant in enumerate(participants):
                if participant.get("participant_id") != clean_participant_id:
                    continue
                updated = {**participant, "status": clean_status, "updated_at": _now()}
                participants[index] = updated
                break
            if updated is None:
                raise ValueError(f"Participant {clean_participant_id} was not found.")
            self._write_json(self._participants_path(clean_room_id), {"participants": participants})
            event_type = {
                "left": "participant_left",
                "kicked": "participant_kicked",
                "exported": "participant_exported",
                "detached": "session_detached",
            }.get(clean_status)
            if event_type:
                self.append_event(
                    clean_room_id,
                    event_type,
                    participant_id=clean_participant_id,
                    reason=clean_lobby_text(reason, limit=500),
                )
            if clean_status in {"left", "kicked", "exported", "detached"}:
                self.detach_participant_sessions(clean_room_id, clean_participant_id)
            return updated

    def sessions(self, room_id: str) -> list[dict[str, object]]:
        return _read_json_list(self._sessions_path(_clean_room_id(room_id)), "sessions")

    def session(self, room_id: str, session_id: str) -> dict[str, object]:
        clean_session_id = _clean_session_id(session_id)
        for session in self.sessions(room_id):
            if session.get("session_id") == clean_session_id:
                return session
        return {}

    def upsert_session(self, room_id: str, session: dict[str, object]) -> tuple[dict[str, object], bool]:
        clean_room_id = _clean_room_id(room_id)
        clean_session_id = _clean_session_id(session.get("session_id"))
        if not clean_session_id:
            raise ValueError("session_id is required.")
        incoming = dict(session)
        incoming["room_id"] = clean_room_id
        incoming["session_id"] = clean_session_id
        incoming["status"] = _session_status(incoming.get("status") or "attached")
        now = _now()
        with self._lock:
            sessions = self.sessions(clean_room_id)
            created = True
            for index, existing in enumerate(sessions):
                if existing.get("session_id") != clean_session_id:
                    continue
                created = False
                merged = {
                    **existing,
                    **{key: value for key, value in incoming.items() if value not in ("", None, [], {})},
                    "updated_at": now,
                }
                sessions[index] = merged
                self._write_json(self._sessions_path(clean_room_id), {"sessions": sessions})
                return merged, created
            incoming.setdefault("created_at", now)
            incoming["updated_at"] = now
            sessions.append(incoming)
            self._write_json(self._sessions_path(clean_room_id), {"sessions": sessions})
            return incoming, created

    def detach_participant_sessions(self, room_id: str, participant_id: str) -> list[dict[str, object]]:
        clean_room_id = _clean_room_id(room_id)
        clean_participant_id = _clean_participant_id(participant_id)
        sessions = self.sessions(clean_room_id)
        detached = []
        changed = False
        for index, session in enumerate(sessions):
            if session.get("participant_id") != clean_participant_id:
                continue
            updated = {**session, "status": "detached", "updated_at": _now()}
            sessions[index] = updated
            detached.append(updated)
            changed = True
        if changed:
            self._write_json(self._sessions_path(clean_room_id), {"sessions": sessions})
        return detached

    def append_event(self, room_id: str, event_type: str, **payload: object) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        event = {
            "id": uuid4().hex[:12],
            "created_at": _now(),
            "room_id": clean_room_id,
            "type": _clean_event_type(event_type),
            **{key: value for key, value in payload.items() if value not in (None, "", [], {})},
        }
        path = self._events_path(clean_room_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        return event

    def read_events(self, room_id: str, *, after: str = "") -> list[dict[str, object]]:
        path = self._events_path(_clean_room_id(room_id))
        if not path.exists():
            return []
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        if after:
            for index, event in enumerate(events):
                if str(event.get("id") or "") == after:
                    return events[index + 1 :]
        return events

    def attach_media(
        self,
        room_id: str,
        *,
        filename: str,
        content_type: str,
        size: int = 0,
        supported: bool,
        data: bytes = b"",
    ) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        media_id = uuid4().hex[:12]
        safe_filename = _safe_media_filename(filename) or media_id
        media_dir = self._media_dir(clean_room_id) / media_id
        media_path = media_dir / safe_filename
        if data:
            media_dir.mkdir(parents=True, exist_ok=True)
            media_path.write_bytes(data)
            size = len(data)
        else:
            media_dir.mkdir(parents=True, exist_ok=True)
        media = {
            "id": media_id,
            "filename": safe_filename,
            "content_type": clean_lobby_text(content_type, limit=128) or "application/octet-stream",
            "size": max(0, int(size or 0)),
            "path": str(media_path),
            "supported": bool(supported),
        }
        self.append_event(
            clean_room_id,
            "media_attached" if supported else "unsupported_media",
            media=media,
        )
        return media

    def set_room_status(self, room_id: str, status: str) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        clean_status = _room_status(status)
        with self._lock:
            room = self.room(clean_room_id)
            if not room:
                raise ValueError(f"Room {clean_room_id} was not found.")
            room = {**room, "status": clean_status, "updated_at": _now()}
            self._write_json(self._room_path(clean_room_id), room)
            if clean_status == "archived":
                self.append_event(clean_room_id, "room_archived")
            elif clean_status == "closed":
                self.append_event(clean_room_id, "room_closed")
            return room

    def export_participant(self, room_id: str, participant_id: str, *, reason: str = "") -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        clean_participant_id = _clean_participant_id(participant_id)
        participant = self.set_participant_status(clean_room_id, clean_participant_id, "exported", reason=reason)
        handoff_dir = self._room_dir(clean_room_id) / "handoffs"
        handoff_dir.mkdir(parents=True, exist_ok=True)
        packet_path = handoff_dir / f"{clean_participant_id}.md"
        packet = (
            f"# Agent Session Handoff\n\n"
            f"- Room: {clean_room_id}\n"
            f"- Participant: {clean_participant_id}\n"
            f"- Status: exported\n"
            f"- Reason: {clean_lobby_text(reason, limit=500)}\n"
        )
        packet_path.write_text(packet, encoding="utf-8")
        return {"participant": participant, "handoff_packet_path": str(packet_path)}

    def room_payload(self, room_id: str) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        return {
            "room": self.room(clean_room_id),
            "participants": self.participants(clean_room_id),
            "sessions": self.sessions(clean_room_id),
            "events": self.read_events(clean_room_id),
        }

    def _room_dir(self, room_id: str) -> Path:
        return self.rooms_root / room_id

    def _room_path(self, room_id: str) -> Path:
        return self._room_dir(room_id) / "room.json"

    def _participants_path(self, room_id: str) -> Path:
        return self._room_dir(room_id) / "participants.json"

    def _sessions_path(self, room_id: str) -> Path:
        return self._room_dir(room_id) / "sessions.json"

    def _events_path(self, room_id: str) -> Path:
        return self._room_dir(room_id) / "events.jsonl"

    def _media_dir(self, room_id: str) -> Path:
        return self._room_dir(room_id) / "media"

    @staticmethod
    def _write_json(path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_json_list(path: Path, key: str) -> list[dict[str, object]]:
    payload = _read_json_object(path)
    items = payload.get(key)
    return [dict(item) for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _clean_room_id(value: object) -> str:
    room_id = clean_lobby_text(value, limit=128)
    if not room_id or room_id in {".", ".."} or "/" in room_id or "\\" in room_id or Path(room_id).name != room_id:
        raise ValueError("room_id is required.")
    return room_id


def _clean_participant_id(value: object) -> str:
    return clean_lobby_text(value, limit=128)


def _clean_session_id(value: object) -> str:
    return clean_lobby_text(value, limit=128)


def _clean_event_type(value: object) -> str:
    event_type = clean_lobby_text(value, limit=64)
    if not event_type:
        raise ValueError("event type is required.")
    return event_type


def _safe_media_filename(value: object) -> str:
    name = Path(clean_lobby_text(value, limit=256)).name
    if name in {"", ".", ".."}:
        return ""
    return name.replace("/", "_").replace("\\", "_")


def _room_status(value: object) -> str:
    status = clean_lobby_text(value, limit=32) or "active"
    if status not in ROOM_STATUSES:
        raise ValueError(f"Unsupported room status: {status}")
    return status


def _participant_status(value: object) -> str:
    status = clean_lobby_text(value, limit=32) or "joined"
    if status not in PARTICIPANT_STATUSES:
        raise ValueError(f"Unsupported participant status: {status}")
    return status


def _session_status(value: object) -> str:
    status = clean_lobby_text(value, limit=32) or "attached"
    if status not in SESSION_STATUSES:
        raise ValueError(f"Unsupported session status: {status}")
    return status
