from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

LobbySide = Literal["mine", "my-agent", "other", "other-agent"]
LobbyKind = Literal["message", "ready", "deploy"]
RoomChannel = Literal["lobby", "side_chat", "official", "system", "review"]
MeetingEventKind = Literal[
    "meeting_started",
    "role_sessions_started",
    "research_completed",
    "free_chat_recorded",
    "debate_completed",
    "synthesis_skipped",
    "synthesis_completed",
    "artifacts_written",
]
LiveEventKind = Literal[
    "status",
    "research",
    "message",
    "room_chat",
    "synthesis",
    "artifact",
    "live_agent_turn_request",
]

LOBBY_SIDES: set[str] = {"mine", "my-agent", "other", "other-agent"}
LOBBY_KINDS: set[str] = {"message", "ready", "deploy"}
LOBBY_CHANNELS: set[str] = {"lobby", "side_chat"}
OFFICIAL_LIVE_KINDS: set[str] = {"message", "synthesis"}
JSONL_TAIL_BLOCK_BYTES = 8192


@dataclass(frozen=True)
class LobbyEvent:
    id: str
    created_at: str
    name: str
    side: LobbySide
    kind: LobbyKind
    message: str
    channel: Literal["lobby", "side_chat"] = "lobby"
    audience: str = "room"
    official_record: bool = False
    actor_id: str = ""
    source_event_id: str = ""
    auto_chain_depth: int = 0
    live_agent_endpoint: bool = False

    @classmethod
    def from_payload(cls, payload: dict[str, object], channel: Literal["lobby", "side_chat"] = "lobby") -> LobbyEvent:
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
            channel=channel,
            actor_id=clean_lobby_text(payload.get("actor_id", ""), limit=64),
            source_event_id=clean_lobby_text(payload.get("source_event_id", ""), limit=128),
            auto_chain_depth=normalize_chain_depth(payload.get("auto_chain_depth")),
        )

    @classmethod
    def from_json_line(cls, line: str, default_channel: Literal["lobby", "side_chat"] = "lobby") -> LobbyEvent | None:
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
            channel=normalize_lobby_channel(payload.get("channel"), default=default_channel),
            audience=clean_lobby_text(payload.get("audience", "room"), limit=32) or "room",
            official_record=False,
            actor_id=clean_lobby_text(payload.get("actor_id", ""), limit=64),
            source_event_id=clean_lobby_text(payload.get("source_event_id", ""), limit=128),
            auto_chain_depth=normalize_chain_depth(payload.get("auto_chain_depth")),
            live_agent_endpoint=payload.get("live_agent_endpoint") is True,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "name": self.name,
            "side": self.side,
            "kind": self.kind,
            "message": self.message,
            "channel": self.channel,
            "audience": self.audience,
            "official_record": self.official_record,
            "actor_id": self.actor_id,
            "source_event_id": self.source_event_id,
            "auto_chain_depth": self.auto_chain_depth,
            "live_agent_endpoint": self.live_agent_endpoint,
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


def read_lobby_events(path: Path, limit: int | None = 80) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return _read_lobby_event_tail(path, limit=limit, default_channel="lobby")


def read_side_chat_events(path: Path, limit: int | None = 120) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return _read_lobby_event_tail(path, limit=limit, default_channel="side_chat")


def read_lobby_events_after(path: Path, last_event_id: str | None, limit: int = 80) -> list[dict[str, object]]:
    return _events_after_id(read_lobby_events(path, limit=limit), last_event_id)


def read_side_chat_events_after(path: Path, last_event_id: str | None, limit: int = 120) -> list[dict[str, object]]:
    return _events_after_id(read_side_chat_events(path, limit=limit), last_event_id)


def append_lobby_event_to_file(path: Path, payload: dict[str, object], *, live_agent_endpoint: bool = False) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = LobbyEvent.from_payload(payload)
    event_payload = event.to_dict()
    event_payload["live_agent_endpoint"] = live_agent_endpoint
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event_payload, ensure_ascii=False, sort_keys=True) + "\n")
    return event_payload


def append_side_chat_event_to_file(path: Path, payload: dict[str, object]) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = LobbyEvent.from_payload(payload, channel="side_chat")
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return event.to_dict()


def append_live_event(meeting_dir: Path, payload: dict[str, object]) -> dict[str, object]:
    kind = str(payload.get("kind", "status"))
    turn_index = payload.get("turn_index")
    official_record = payload.get("official_record")
    event = {
        "id": uuid4().hex[:12],
        "created_at": datetime.now(UTC).isoformat(),
        "kind": kind,
        "meeting_id": clean_lobby_text(payload.get("meeting_id", ""), limit=128),
        "channel": _payload_live_channel(kind, payload),
        "audience": clean_lobby_text(payload.get("audience", "room"), limit=32) or "room",
        "official_record": official_record is True if isinstance(official_record, bool) else kind in OFFICIAL_LIVE_KINDS,
        "actor_id": clean_lobby_text(payload.get("actor_id", ""), limit=64),
        "target_agent_id": clean_lobby_text(payload.get("target_agent_id", ""), limit=64),
        "source_event_id": clean_lobby_text(payload.get("source_event_id", ""), limit=128),
        "review_checkpoint_id": clean_lobby_text(payload.get("review_checkpoint_id", ""), limit=128),
        "role_id": payload.get("role_id"),
        "display_name": payload.get("display_name"),
        "round": payload.get("round"),
        "turn_id": clean_lobby_text(payload.get("turn_id", ""), limit=128),
        "turn_index": turn_index if isinstance(turn_index, int) and not isinstance(turn_index, bool) else None,
        "engagement_mode": clean_lobby_text(payload.get("engagement_mode", ""), limit=64),
        "content": clean_lobby_text(payload.get("content", ""), limit=4000),
        "position": clean_lobby_text(payload.get("position", ""), limit=1000),
        "stance_status": payload.get("stance_status"),
        "stance_delta": payload.get("stance_delta"),
        "changed_by": payload.get("changed_by") if isinstance(payload.get("changed_by"), list) else [],
        "change_reason": clean_lobby_text(payload.get("change_reason", ""), limit=1000),
        "remaining_resistance": clean_lobby_text(payload.get("remaining_resistance", ""), limit=1000),
        "emotion": payload.get("emotion") if isinstance(payload.get("emotion"), dict) else {},
        "confidence": payload.get("confidence"),
        "retry_status": payload.get("retry_status"),
        "retry_attempts": payload.get("retry_attempts"),
    }
    path = meeting_dir / "live_events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event


def read_live_events(meeting_dir: Path, limit: int | None = 200) -> list[dict[str, object]]:
    path = meeting_dir / "live_events.jsonl"
    if not path.exists():
        return []
    if limit is not None:
        return _read_live_event_tail(path, limit=limit)
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
    return events


def read_live_events_after(meeting_dir: Path, last_event_id: str | None, limit: int = 200) -> list[dict[str, object]]:
    return _events_after_id(read_live_events(meeting_dir, limit=limit), last_event_id)


def _read_lobby_event_tail(
    path: Path,
    *,
    limit: int | None,
    default_channel: Literal["lobby", "side_chat"],
) -> list[dict[str, object]]:
    if limit is not None and limit <= 0:
        return []
    entries: list[dict[str, object]] = []
    for line in _jsonl_tail_lines_newest_first(path):
        event = LobbyEvent.from_json_line(line, default_channel=default_channel)
        if event is None:
            continue
        entries.append(event.to_dict())
        if limit is not None and len(entries) >= limit:
            break
    entries.reverse()
    return entries


def _read_live_event_tail(path: Path, *, limit: int) -> list[dict[str, object]]:
    if limit <= 0:
        return []
    events: list[dict[str, object]] = []
    for line in _jsonl_tail_lines_newest_first(path):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
            if len(events) >= limit:
                break
    events.reverse()
    return events


def _jsonl_tail_lines_newest_first(path: Path):
    with path.open("rb") as file:
        file.seek(0, 2)
        position = file.tell()
        buffer = b""
        while position > 0:
            read_size = min(JSONL_TAIL_BLOCK_BYTES, position)
            position -= read_size
            file.seek(position)
            chunk = file.read(read_size)
            parts = (chunk + buffer).split(b"\n")
            if position > 0:
                buffer = parts[0]
                complete_lines = parts[1:]
            else:
                buffer = b""
                complete_lines = parts
            for line in reversed(complete_lines):
                if line.strip():
                    yield line.decode("utf-8", errors="ignore")


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


def normalize_lobby_channel(value: object, default: Literal["lobby", "side_chat"] = "lobby") -> Literal["lobby", "side_chat"]:
    return value if value in LOBBY_CHANNELS else default  # type: ignore[return-value]


def normalize_chain_depth(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    return 0


def _live_channel(kind: str) -> RoomChannel:
    if kind in OFFICIAL_LIVE_KINDS:
        return "official"
    if kind == "room_chat":
        return "side_chat"
    return "system"


def _payload_live_channel(kind: str, payload: dict[str, object]) -> RoomChannel:
    channel = clean_lobby_text(payload.get("channel", ""), limit=32)
    if channel in {"official", "system", "side_chat", "review"}:
        return channel  # type: ignore[return-value]
    return _live_channel(kind)


def _events_after_id(events: list[dict[str, object]], last_event_id: str | None) -> list[dict[str, object]]:
    if not last_event_id:
        return events
    for index, event in enumerate(events):
        if event.get("id") == last_event_id:
            return events[index + 1 :]
    return events
