from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

LobbySide = Literal["mine", "my-agent", "other", "other-agent"]
LobbyKind = Literal["message", "ready", "deploy"]
MeetingEventKind = Literal[
    "meeting_started",
    "role_sessions_started",
    "research_completed",
    "debate_completed",
    "synthesis_completed",
    "artifacts_written",
]
LiveEventKind = Literal["status", "research", "message", "synthesis", "artifact"]

LOBBY_SIDES: set[str] = {"mine", "my-agent", "other", "other-agent"}
LOBBY_KINDS: set[str] = {"message", "ready", "deploy"}


@dataclass(frozen=True)
class LobbyEvent:
    id: str
    created_at: str
    name: str
    side: LobbySide
    kind: LobbyKind
    message: str

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> LobbyEvent:
        kind = payload.get("kind") if payload.get("kind") in LOBBY_KINDS else "message"
        message = clean_lobby_text(payload.get("message", ""), limit=240)
        if kind == "ready" and not message:
            message = "준비됐습니다."
        if kind == "deploy" and not message:
            message = "deploy 대기 상태로 전환했습니다."
        return cls(
            id=uuid4().hex[:12],
            created_at=datetime.now(UTC).isoformat(),
            name=clean_lobby_text(payload.get("name", "guest"), limit=32) or "guest",
            side=normalize_lobby_side(payload.get("side")),
            kind=kind,  # type: ignore[arg-type]
            message=message,
        )

    @classmethod
    def from_json_line(cls, line: str) -> LobbyEvent | None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return cls(
            id=str(payload.get("id") or uuid4().hex[:12]),
            created_at=str(payload.get("created_at") or ""),
            name=clean_lobby_text(payload.get("name", "guest"), limit=32) or "guest",
            side=normalize_lobby_side(payload.get("side")),
            kind=normalize_lobby_kind(payload.get("kind")),
            message=clean_lobby_text(payload.get("message", ""), limit=240),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "name": self.name,
            "side": self.side,
            "kind": self.kind,
            "message": self.message,
        }


@dataclass(frozen=True)
class MeetingEvent:
    kind: MeetingEventKind
    message: str
    actor_id: str = "system"
    scope: Literal["meeting"] = "meeting"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    payload: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "created_at": self.created_at,
            "scope": self.scope,
            "kind": self.kind,
            "actor_id": self.actor_id,
            "message": self.message,
            "payload": self.payload,
        }


class MeetingEventLog:
    def __init__(self) -> None:
        self._events: list[MeetingEvent] = []

    def add(self, kind: MeetingEventKind, message: str, **payload: object) -> None:
        self._events.append(MeetingEvent(kind=kind, message=message, payload=payload))

    def to_list(self) -> list[dict[str, object]]:
        return [event.to_dict() for event in self._events]


def read_lobby_events(path: Path, limit: int = 80) -> list[dict[str, object]]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = LobbyEvent.from_json_line(line)
        if event is not None:
            entries.append(event.to_dict())
    return entries[-limit:]


def read_side_chat_events(path: Path, limit: int = 120) -> list[dict[str, object]]:
    return read_lobby_events(path, limit=limit)


def append_lobby_event_to_file(path: Path, payload: dict[str, object]) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = LobbyEvent.from_payload(payload)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return event.to_dict()


def append_side_chat_event_to_file(path: Path, payload: dict[str, object]) -> dict[str, object]:
    return append_lobby_event_to_file(path, payload)


def append_live_event(meeting_dir: Path, payload: dict[str, object]) -> dict[str, object]:
    event = {
        "id": uuid4().hex[:12],
        "created_at": datetime.now(UTC).isoformat(),
        "kind": payload.get("kind", "status"),
        "role_id": payload.get("role_id"),
        "display_name": payload.get("display_name"),
        "round": payload.get("round"),
        "content": clean_lobby_text(payload.get("content", ""), limit=4000),
        "position": clean_lobby_text(payload.get("position", ""), limit=1000),
        "stance_status": payload.get("stance_status"),
        "stance_delta": payload.get("stance_delta"),
        "changed_by": payload.get("changed_by") if isinstance(payload.get("changed_by"), list) else [],
        "change_reason": clean_lobby_text(payload.get("change_reason", ""), limit=1000),
        "remaining_resistance": clean_lobby_text(payload.get("remaining_resistance", ""), limit=1000),
        "emotion": payload.get("emotion") if isinstance(payload.get("emotion"), dict) else {},
        "confidence": payload.get("confidence"),
    }
    path = meeting_dir / "live_events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event


def read_live_events(meeting_dir: Path, limit: int = 200) -> list[dict[str, object]]:
    path = meeting_dir / "live_events.jsonl"
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
    return events[-limit:]


def write_live_state(meeting_dir: Path, payload: dict[str, object]) -> None:
    (meeting_dir / "live_state.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def clean_lobby_text(value: object, limit: int) -> str:
    return str(value or "").replace("\n", " ").replace("\r", " ").strip()[:limit]


def normalize_lobby_side(value: object) -> LobbySide:
    return value if value in LOBBY_SIDES else "other"  # type: ignore[return-value]


def normalize_lobby_kind(value: object) -> LobbyKind:
    return value if value in LOBBY_KINDS else "message"  # type: ignore[return-value]
