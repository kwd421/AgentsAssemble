from __future__ import annotations

import json
import shutil
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agentsassemble.meeting_events import clean_lobby_text

ROOM_STATUSES = {"active", "closed", "archived"}
PARTICIPANT_STATUSES = {"joined", "left", "kicked", "exported", "detached"}
SESSION_STATUSES = {"available", "attached", "detached", "unavailable", "error"}
ACTIVE_PARTICIPANT_STATUSES = {"joined"}

_STORE_REGISTRY_LOCK = threading.RLock()
_STORE_LOCKS: dict[str, threading.RLock] = {}
_EVENT_LISTENERS: dict[str, list[Callable[[dict[str, object]], None]]] = {}
_EVENT_NEXT_SEQUENCE: dict[str, int] = {}


def _store_lock(output_root: Path) -> threading.RLock:
    key = str(output_root.expanduser().resolve())
    with _STORE_REGISTRY_LOCK:
        return _STORE_LOCKS.setdefault(key, threading.RLock())


def _event_path_key(path: Path) -> str:
    return str(path.expanduser().resolve())


class RoomStore:
    """Small file-backed source of truth for active room/session state."""

    def __init__(self, output_root: Path) -> None:
        self.output_root = Path(output_root)
        self.rooms_root = self.output_root / "rooms"
        self._lock = _store_lock(self.output_root)

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

    def update_session_fields(self, room_id: str, session_id: str, **updates: object) -> dict[str, object]:
        """Update an existing session while preserving explicit empty values.

        ``upsert_session`` intentionally ignores empty incoming values for legacy
        merge callers. Runtime lifecycle transitions need the opposite behavior:
        stopping a process must be able to clear its pid, active turn, and error.
        """
        clean_room_id = _clean_room_id(room_id)
        clean_session_id = _clean_session_id(session_id)
        with self._lock:
            sessions = self.sessions(clean_room_id)
            for index, session in enumerate(sessions):
                if session.get("session_id") != clean_session_id:
                    continue
                updated = {**session, **updates, "updated_at": _now()}
                if "status" in updates:
                    updated["status"] = _session_status(updates.get("status"))
                sessions[index] = updated
                self._write_json(self._sessions_path(clean_room_id), {"sessions": sessions})
                return updated
        raise ValueError(f"Session {clean_session_id} was not found.")

    def update_participant_fields(self, room_id: str, participant_id: str, **updates: object) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        clean_participant_id = _clean_participant_id(participant_id)
        with self._lock:
            participants = self.participants(clean_room_id)
            for index, participant in enumerate(participants):
                if participant.get("participant_id") != clean_participant_id:
                    continue
                updated = {**participant, **updates, "updated_at": _now()}
                if "status" in updates:
                    updated["status"] = _participant_status(updates.get("status"))
                participants[index] = updated
                self._write_json(self._participants_path(clean_room_id), {"participants": participants})
                return updated
        raise ValueError(f"Participant {clean_participant_id} was not found.")

    def command_result(self, room_id: str, request_id: str) -> dict[str, object]:
        clean_request_id = clean_lobby_text(request_id, limit=128)
        if not clean_request_id:
            return {}
        with self._lock:
            commands = _read_json_list(self._commands_path(_clean_room_id(room_id)), "commands")
        for command in reversed(commands):
            if command.get("request_id") == clean_request_id and isinstance(command.get("result"), dict):
                return dict(command["result"])
        return {}

    def record_command_result(
        self,
        room_id: str,
        request_id: str,
        result: dict[str, object],
        *,
        max_entries: int = 500,
    ) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        clean_request_id = clean_lobby_text(request_id, limit=128)
        if not clean_request_id:
            raise ValueError("request_id is required.")
        with self._lock:
            existing = self.command_result(clean_room_id, clean_request_id)
            if existing:
                return existing
            commands = _read_json_list(self._commands_path(clean_room_id), "commands")
            commands.append({"request_id": clean_request_id, "created_at": _now(), "result": dict(result)})
            commands = commands[-max(1, int(max_entries or 500)) :]
            self._write_json(self._commands_path(clean_room_id), {"commands": commands})
        return dict(result)

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
        path = self._events_path(clean_room_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path_key = _event_path_key(path)
        with self._lock:
            sequence = _next_event_sequence(path)
            clean_payload = {
                key: value
                for key, value in payload.items()
                if key not in {"v", "id", "seq", "room_id", "type", "created_at", "actor"}
                and value not in (None, "", [], {})
            }
            participant_id = clean_lobby_text(
                payload.get("participant_id") or payload.get("actor_id"),
                limit=128,
            )
            participant_type = clean_lobby_text(
                payload.get("participant_type") or payload.get("actor_type"),
                limit=32,
            )
            if participant_type == "user":
                participant_type = "human"
            if participant_id and not participant_type:
                participant_type = "agent" if payload.get("participant_id") else "human"
            event = {
                "v": 1,
                "id": uuid4().hex[:12],
                "seq": sequence,
                "created_at": _now(),
                "room_id": clean_room_id,
                "type": _clean_event_type(event_type),
                "actor": {
                    "participant_id": participant_id,
                    "participant_type": participant_type,
                },
                **clean_payload,
            }
            with path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            with _STORE_REGISTRY_LOCK:
                listeners = list(_EVENT_LISTENERS.get(path_key, []))
        for listener in listeners:
            try:
                listener(dict(event))
            except Exception:
                continue
        return event

    def read_events(
        self,
        room_id: str,
        *,
        after: str = "",
        after_seq: int = 0,
    ) -> list[dict[str, object]]:
        clean_room_id = _clean_room_id(room_id)
        path = self._events_path(clean_room_id)
        if not path.exists():
            return []
        with self._lock:
            events = _read_canonical_events(path, clean_room_id)
        if after_seq:
            return [event for event in events if _safe_int(event.get("seq")) > max(0, int(after_seq))]
        if after:
            for index, event in enumerate(events):
                if str(event.get("id") or "") == after:
                    return events[index + 1 :]
        return events

    def latest_event_sequence(self, room_id: str) -> int:
        events = self.read_events(room_id)
        return _safe_int(events[-1].get("seq")) if events else 0

    def add_event_listener(
        self,
        room_id: str,
        listener: Callable[[dict[str, object]], None],
    ) -> Callable[[], None]:
        path_key = _event_path_key(self._events_path(_clean_room_id(room_id)))
        with _STORE_REGISTRY_LOCK:
            _EVENT_LISTENERS.setdefault(path_key, []).append(listener)

        def remove() -> None:
            with _STORE_REGISTRY_LOCK:
                listeners = _EVENT_LISTENERS.get(path_key, [])
                if listener in listeners:
                    listeners.remove(listener)
                if not listeners:
                    _EVENT_LISTENERS.pop(path_key, None)

        return remove

    def canonicalize_events(self, room_id: str) -> dict[str, object]:
        clean_room_id = _clean_room_id(room_id)
        path = self._events_path(clean_room_id)
        if not path.exists():
            return {"room_id": clean_room_id, "migrated": False, "event_count": 0, "backup_path": ""}
        with self._lock:
            raw_lines = path.read_text(encoding="utf-8").splitlines()
            raw_events = _json_objects(raw_lines)
            canonical = [
                _canonical_event_from_record(record, clean_room_id, index)
                for index, record in enumerate(raw_events, start=1)
            ]
            canonical = [event for event in canonical if event]
            migrated = any(
                "event_id" in record
                or "kind" in record
                or "id" not in record
                or "type" not in record
                or "seq" not in record
                or "v" not in record
                or "actor" not in record
                for record in raw_events
            )
            backup_path = path.with_name("events.pre-unification.jsonl")
            if migrated:
                if not backup_path.exists():
                    shutil.copyfile(path, backup_path)
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_text(
                    "".join(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n" for event in canonical),
                    encoding="utf-8",
                )
                tmp.replace(path)
            path_key = _event_path_key(path)
            with _STORE_REGISTRY_LOCK:
                _EVENT_NEXT_SEQUENCE[path_key] = max(
                    [_safe_int(event.get("seq")) for event in canonical] or [0]
                )
        return {
            "room_id": clean_room_id,
            "migrated": migrated,
            "event_count": len(canonical),
            "backup_path": str(backup_path) if migrated else "",
        }

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

    def _commands_path(self, room_id: str) -> Path:
        return self._room_dir(room_id) / "commands.json"

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


def _json_objects(lines: list[str]) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _read_canonical_events(path: Path, room_id: str) -> list[dict[str, object]]:
    records = _json_objects(path.read_text(encoding="utf-8").splitlines())
    return [
        event
        for index, record in enumerate(records, start=1)
        if (event := _canonical_event_from_record(record, room_id, index))
    ]


def _canonical_event_from_record(
    record: dict[str, object],
    room_id: str,
    sequence: int,
) -> dict[str, object]:
    if record.get("id") and record.get("type"):
        event = dict(record)
        event["v"] = 1
        event["seq"] = _safe_int(record.get("seq")) or sequence
        event["room_id"] = clean_lobby_text(record.get("room_id"), limit=128) or room_id
        actor = record.get("actor") if isinstance(record.get("actor"), dict) else {}
        participant_id = clean_lobby_text(
            actor.get("participant_id") or record.get("participant_id") or record.get("actor_id"),
            limit=128,
        )
        participant_type = clean_lobby_text(
            actor.get("participant_type") or record.get("participant_type") or record.get("actor_type"),
            limit=32,
        )
        if participant_type == "user":
            participant_type = "human"
        if participant_id and not participant_type:
            participant_type = "agent" if record.get("participant_id") else "human"
        event["actor"] = {
            "participant_id": participant_id,
            "participant_type": participant_type,
        }
        return event

    legacy_id = clean_lobby_text(record.get("event_id"), limit=128)
    legacy_kind = clean_lobby_text(record.get("kind"), limit=64)
    if not legacy_id or not legacy_kind:
        return {}
    event_type = {
        "user_message": "message_final",
        "agent_message": "message_final",
        "agent_delta": "message_delta",
        "agent_error": "error",
        "agent_input": "agent_input",
        "system": "system",
    }.get(legacy_kind, legacy_kind)
    participant_id = clean_lobby_text(record.get("actor_id"), limit=128)
    participant_type = clean_lobby_text(record.get("actor_type"), limit=32)
    if participant_type == "user":
        participant_type = "human"
    metadata = dict(record.get("metadata")) if isinstance(record.get("metadata"), dict) else {}
    event: dict[str, object] = {
        "v": 1,
        "id": legacy_id,
        "seq": sequence,
        "created_at": clean_lobby_text(record.get("created_at"), limit=128) or _now(),
        "room_id": clean_lobby_text(record.get("room_id"), limit=128) or room_id,
        "type": event_type,
        "actor": {
            "participant_id": participant_id,
            "participant_type": participant_type or ("agent" if legacy_kind.startswith("agent_") else "human"),
        },
        "actor_id": participant_id,
        "actor_type": participant_type or ("agent" if legacy_kind.startswith("agent_") else "human"),
        "content": clean_lobby_text(record.get("content"), limit=12000),
    }
    if metadata:
        event["metadata"] = metadata
        source_event_id = clean_lobby_text(metadata.get("source_event_id"), limit=128)
        if source_event_id:
            event["source_event_id"] = source_event_id
    return {key: value for key, value in event.items() if value not in (None, "", [], {})}


def _next_event_sequence(path: Path) -> int:
    path_key = _event_path_key(path)
    with _STORE_REGISTRY_LOCK:
        current = _EVENT_NEXT_SEQUENCE.get(path_key)
    if current is None:
        current = 0
        if path.exists():
            records = _json_objects(path.read_text(encoding="utf-8").splitlines())
            current = max(
                [_safe_int(record.get("seq")) or index for index, record in enumerate(records, start=1)] or [0]
            )
    next_sequence = current + 1
    with _STORE_REGISTRY_LOCK:
        _EVENT_NEXT_SEQUENCE[path_key] = next_sequence
    return next_sequence


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


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
