from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from agentsassemble.room.text import clean_room_text, has_room_visible_text

LobbySide = Literal["mine", "my-agent", "other", "other-agent"]
LobbyKind = Literal["message", "ready", "deploy", "vote", "vote_cast", "thinking"]
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
    "promoted_context",
    "artifact",
    "live_agent_turn_request",
    "live_agent_turn_cancelled",
]

LOBBY_SIDES: set[str] = {"mine", "my-agent", "other", "other-agent"}
LOBBY_KINDS: set[str] = {"message", "ready", "deploy", "vote", "vote_cast", "thinking"}
VOTE_MAX_OPTIONS = 10
VOTE_OPTION_LIMIT = 100
VOTE_QUESTION_LIMIT = 300
LOBBY_CHANNELS: set[str] = {"lobby", "side_chat"}
OFFICIAL_LIVE_KINDS: set[str] = {"message", "synthesis", "promoted_context"}
JSONL_TAIL_BLOCK_BYTES = 8192
LOBBY_MESSAGE_LIMIT = 2000
ROOM_TOPIC_LIMIT = LOBBY_MESSAGE_LIMIT


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
    actor_type: str = ""  # "human" | "agent" | "" (unknown/legacy) — stamped server-side
    target_agent_id: str = ""
    source_event_id: str = ""
    thread_source_event_id: str = ""
    auto_chain_depth: int = 0
    live_agent_endpoint: bool = False
    flow_id: str = ""
    flow_meeting_id: str = ""
    flow_event_type: str = ""
    flow_status: str = ""
    flow_topic: str = ""
    flow_policy: str = ""
    flow_action: str = ""
    flow_reason: str = ""
    flow_runtime_mode: str = ""
    flow_duration_seconds: int = 0
    flow_tick_interval: int = 0
    flow_cooldown: int = 0
    flow_max_agent_turns: int = 0
    flow_max_total_turns: int = 0
    flow_max_silence_seconds: int = 0
    flow_total_turns: int = 0
    flow_agent_count: int = 0
    flow_turn_delivery_ms: int = 0
    flow_provider_invocation_ms: int = 0
    flow_reply_post_ms: int = 0
    flow_started_at: str = ""
    flow_deadline_at: str = ""
    # Poll events (kind "vote" carries question/options; kind "vote_cast" is
    # one ballot referencing vote_id — latest cast per voter wins).
    vote_id: str = ""
    vote_question: str = ""
    vote_options: list[str] = field(default_factory=list)
    vote_choice: str = ""
    attachments: list[dict[str, object]] = field(default_factory=list)

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, object],
        channel: Literal["lobby", "side_chat"] = "lobby",
        *,
        message_limit: int = LOBBY_MESSAGE_LIMIT,
    ) -> LobbyEvent:
        kind = payload.get("kind") if payload.get("kind") in LOBBY_KINDS else "message"
        message = clean_lobby_text(payload.get("message", ""), limit=message_limit)
        if kind == "ready" and not message:
            message = "준비됐습니다."
        if kind == "deploy" and not message:
            message = "deploy 대기 상태로 전환했습니다."
        event_id = uuid4().hex[:12]
        vote_id = clean_lobby_text(payload.get("vote_id", ""), limit=128)
        vote_question = clean_lobby_text(payload.get("vote_question", ""), limit=VOTE_QUESTION_LIMIT)
        vote_options = clean_vote_options(payload.get("vote_options"))
        vote_choice = clean_lobby_text(payload.get("vote_choice", ""), limit=VOTE_OPTION_LIMIT)
        if kind == "vote":
            # A poll needs a question and at least two options; otherwise it is
            # just a message. The poll's own event id doubles as its vote_id.
            if not vote_question or len(vote_options) < 2:
                kind = "message"
            else:
                vote_id = vote_id or event_id
                if not message:
                    numbered = " / ".join(
                        f"{index}. {option}" for index, option in enumerate(vote_options, start=1)
                    )
                    message = f"📊 투표: {vote_question} — {numbered}"
        elif kind == "vote_cast":
            # A ballot must reference a poll and pick an option.
            if not vote_id or not vote_choice:
                kind = "message"
            elif not message:
                message = f"🗳️ {vote_choice}"
        return cls(
            id=event_id,
            created_at=datetime.now(UTC).isoformat(),
            name=clean_lobby_text(payload.get("name", "guest"), limit=32) or "guest",
            side=normalize_lobby_side(payload.get("side")),
            kind=kind,  # type: ignore[arg-type]
            message=message,
            channel=channel,
            actor_id=clean_lobby_text(payload.get("actor_id", ""), limit=64),
            actor_type=normalize_actor_type(payload.get("actor_type"), actor_id=payload.get("actor_id"), side=payload.get("side")),
            target_agent_id=clean_lobby_text(payload.get("target_agent_id", ""), limit=64),
            source_event_id=clean_lobby_text(payload.get("source_event_id", ""), limit=128),
            thread_source_event_id=clean_lobby_text(payload.get("thread_source_event_id", ""), limit=128),
            auto_chain_depth=normalize_chain_depth(payload.get("auto_chain_depth")),
            flow_id=clean_lobby_text(payload.get("flow_id", ""), limit=128),
            flow_meeting_id=clean_lobby_text(payload.get("flow_meeting_id", ""), limit=128),
            flow_event_type=clean_lobby_text(payload.get("flow_event_type", ""), limit=64),
            flow_status=clean_lobby_text(payload.get("flow_status", ""), limit=64),
            flow_topic=clean_lobby_text(payload.get("flow_topic", ""), limit=ROOM_TOPIC_LIMIT),
            flow_policy=clean_lobby_text(payload.get("flow_policy", ""), limit=64),
            flow_action=clean_lobby_text(payload.get("flow_action", ""), limit=64),
            flow_reason=clean_lobby_text(payload.get("flow_reason", ""), limit=400),
            flow_runtime_mode=clean_lobby_text(payload.get("flow_runtime_mode", ""), limit=64),
            flow_duration_seconds=normalize_flow_int(payload.get("flow_duration_seconds")),
            flow_tick_interval=normalize_flow_int(payload.get("flow_tick_interval")),
            flow_cooldown=normalize_flow_int(payload.get("flow_cooldown")),
            flow_max_agent_turns=normalize_flow_int(payload.get("flow_max_agent_turns")),
            flow_max_total_turns=normalize_flow_int(payload.get("flow_max_total_turns")),
            flow_max_silence_seconds=normalize_flow_int(payload.get("flow_max_silence_seconds")),
            flow_total_turns=normalize_flow_int(payload.get("flow_total_turns")),
            flow_agent_count=normalize_flow_int(payload.get("flow_agent_count")),
            flow_turn_delivery_ms=normalize_flow_int(payload.get("flow_turn_delivery_ms")),
            flow_provider_invocation_ms=normalize_flow_int(payload.get("flow_provider_invocation_ms")),
            flow_reply_post_ms=normalize_flow_int(payload.get("flow_reply_post_ms")),
            flow_started_at=clean_lobby_text(payload.get("flow_started_at", ""), limit=64),
            flow_deadline_at=clean_lobby_text(payload.get("flow_deadline_at", ""), limit=64),
            vote_id=vote_id if kind in {"vote", "vote_cast"} else "",
            vote_question=vote_question if kind == "vote" else "",
            vote_options=vote_options if kind == "vote" else [],
            vote_choice=vote_choice if kind == "vote_cast" else "",
            attachments=clean_lobby_attachments(payload.get("attachments")),
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
            message=clean_lobby_text(payload.get("message", ""), limit=LOBBY_MESSAGE_LIMIT),
            channel=normalize_lobby_channel(payload.get("channel"), default=default_channel),
            audience=clean_lobby_text(payload.get("audience", "room"), limit=32) or "room",
            official_record=False,
            actor_id=clean_lobby_text(payload.get("actor_id", ""), limit=64),
            actor_type=clean_lobby_text(payload.get("actor_type", ""), limit=16),
            target_agent_id=clean_lobby_text(payload.get("target_agent_id", ""), limit=64),
            source_event_id=clean_lobby_text(payload.get("source_event_id", ""), limit=128),
            thread_source_event_id=clean_lobby_text(payload.get("thread_source_event_id", ""), limit=128),
            auto_chain_depth=normalize_chain_depth(payload.get("auto_chain_depth")),
            live_agent_endpoint=payload.get("live_agent_endpoint") is True,
            flow_id=clean_lobby_text(payload.get("flow_id", ""), limit=128),
            flow_meeting_id=clean_lobby_text(payload.get("flow_meeting_id", ""), limit=128),
            flow_event_type=clean_lobby_text(payload.get("flow_event_type", ""), limit=64),
            flow_status=clean_lobby_text(payload.get("flow_status", ""), limit=64),
            flow_topic=clean_lobby_text(payload.get("flow_topic", ""), limit=ROOM_TOPIC_LIMIT),
            flow_policy=clean_lobby_text(payload.get("flow_policy", ""), limit=64),
            flow_action=clean_lobby_text(payload.get("flow_action", ""), limit=64),
            flow_reason=clean_lobby_text(payload.get("flow_reason", ""), limit=400),
            flow_runtime_mode=clean_lobby_text(payload.get("flow_runtime_mode", ""), limit=64),
            flow_duration_seconds=normalize_flow_int(payload.get("flow_duration_seconds")),
            flow_tick_interval=normalize_flow_int(payload.get("flow_tick_interval")),
            flow_cooldown=normalize_flow_int(payload.get("flow_cooldown")),
            flow_max_agent_turns=normalize_flow_int(payload.get("flow_max_agent_turns")),
            flow_max_total_turns=normalize_flow_int(payload.get("flow_max_total_turns")),
            flow_max_silence_seconds=normalize_flow_int(payload.get("flow_max_silence_seconds")),
            flow_total_turns=normalize_flow_int(payload.get("flow_total_turns")),
            flow_agent_count=normalize_flow_int(payload.get("flow_agent_count")),
            flow_turn_delivery_ms=normalize_flow_int(payload.get("flow_turn_delivery_ms")),
            flow_provider_invocation_ms=normalize_flow_int(payload.get("flow_provider_invocation_ms")),
            flow_reply_post_ms=normalize_flow_int(payload.get("flow_reply_post_ms")),
            flow_started_at=clean_lobby_text(payload.get("flow_started_at", ""), limit=64),
            flow_deadline_at=clean_lobby_text(payload.get("flow_deadline_at", ""), limit=64),
            vote_id=clean_lobby_text(payload.get("vote_id", ""), limit=128),
            vote_question=clean_lobby_text(payload.get("vote_question", ""), limit=VOTE_QUESTION_LIMIT),
            vote_options=clean_vote_options(payload.get("vote_options")),
            vote_choice=clean_lobby_text(payload.get("vote_choice", ""), limit=VOTE_OPTION_LIMIT),
            attachments=clean_lobby_attachments(payload.get("attachments")),
        )

    def to_dict(self) -> dict[str, object]:
        payload = {
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
            "actor_type": self.actor_type,
            "target_agent_id": self.target_agent_id,
            "source_event_id": self.source_event_id,
            "thread_source_event_id": self.thread_source_event_id,
            "auto_chain_depth": self.auto_chain_depth,
            "live_agent_endpoint": self.live_agent_endpoint,
        }
        for key in (
            "flow_id",
            "flow_meeting_id",
            "flow_event_type",
            "flow_status",
            "flow_topic",
            "flow_policy",
            "flow_action",
            "flow_reason",
            "flow_runtime_mode",
            "flow_started_at",
            "flow_deadline_at",
            "vote_id",
            "vote_question",
            "vote_options",
            "vote_choice",
        ):
            value = getattr(self, key)
            if value:
                payload[key] = value
        # flow_meeting_id alone is mere room scoping carried by every message;
        # only true flow events should serialize their zero-valued counters.
        has_flow_metadata = any(
            getattr(self, key)
            for key in (
                "flow_id",
                "flow_event_type",
                "flow_status",
                "flow_topic",
                "flow_policy",
                "flow_action",
                "flow_reason",
                "flow_runtime_mode",
                "flow_started_at",
                "flow_deadline_at",
            )
        )
        for key in (
            "flow_duration_seconds",
            "flow_tick_interval",
            "flow_cooldown",
            "flow_max_agent_turns",
            "flow_max_total_turns",
            "flow_max_silence_seconds",
            "flow_total_turns",
            "flow_agent_count",
            "flow_turn_delivery_ms",
            "flow_provider_invocation_ms",
            "flow_reply_post_ms",
        ):
            value = getattr(self, key)
            if value or has_flow_metadata:
                payload[key] = value
        if self.attachments:
            payload["attachments"] = self.attachments
        return payload


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


def iter_lobby_events_newest_first(path: Path, default_channel: Literal["lobby", "side_chat"] = "lobby"):
    """Stream lobby events newest-first without loading the whole file.

    Backs the scroll-up history pagination: the JSONL is read backwards in
    blocks, so fetching one page of old messages stays cheap even when the
    log has grown large.
    """
    if not path.exists():
        return
    for line in _jsonl_tail_lines_newest_first(path):
        event = LobbyEvent.from_json_line(line, default_channel=default_channel)
        if event is not None:
            yield event.to_dict()


def read_side_chat_events_after(path: Path, last_event_id: str | None, limit: int = 120) -> list[dict[str, object]]:
    return _events_after_id(read_side_chat_events(path, limit=limit), last_event_id)


FLOW_METADATA_KEYS: set[str] = {
    "flow_id",
    "flow_meeting_id",
    "flow_event_type",
    "flow_status",
    "flow_topic",
    "flow_policy",
    "flow_action",
    "flow_reason",
    "flow_runtime_mode",
    "flow_duration_seconds",
    "flow_tick_interval",
    "flow_cooldown",
    "flow_max_agent_turns",
    "flow_max_total_turns",
    "flow_max_silence_seconds",
    "flow_total_turns",
    "flow_agent_count",
    "flow_turn_delivery_ms",
    "flow_provider_invocation_ms",
    "flow_reply_post_ms",
    "flow_started_at",
    "flow_deadline_at",
}


def append_lobby_event_to_file(
    path: Path,
    payload: dict[str, object],
    *,
    live_agent_endpoint: bool = False,
    allow_flow_metadata: bool = False,
) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    event_payload_input = dict(payload)
    if not allow_flow_metadata:
        for key in FLOW_METADATA_KEYS:
            event_payload_input.pop(key, None)
    event = LobbyEvent.from_payload(
        event_payload_input,
        message_limit=LOBBY_MESSAGE_LIMIT,
    )
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
        "promoted_from": clean_lobby_text(payload.get("promoted_from", ""), limit=64),
        "promoted_from_actor_id": clean_lobby_text(payload.get("promoted_from_actor_id", ""), limit=64),
        "promoted_reason": clean_lobby_text(payload.get("promoted_reason", ""), limit=240),
        "role_id": payload.get("role_id"),
        "display_name": payload.get("display_name"),
        "artifact_kind": clean_lobby_text(payload.get("artifact_kind", ""), limit=64),
        "artifact_path": clean_lobby_text(payload.get("artifact_path", ""), limit=256),
        "artifact_json_path": clean_lobby_text(payload.get("artifact_json_path", ""), limit=256),
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
    return clean_room_text(value, limit)


def clean_vote_options(value: object) -> list[str]:
    """Sanitize poll options: trimmed, deduplicated, capped at VOTE_MAX_OPTIONS."""
    options: list[str] = []
    seen: set[str] = set()
    for item in (value if isinstance(value, list) else []):
        text = clean_lobby_text(item, limit=VOTE_OPTION_LIMIT)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        options.append(text)
        if len(options) >= VOTE_MAX_OPTIONS:
            break
    return options


def clean_lobby_attachments(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    cleaned: list[dict[str, object]] = []
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        attachment_id = clean_lobby_text(item.get("id"), limit=64)
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", attachment_id):
            continue
        filename = clean_lobby_text(item.get("filename"), limit=120) or "attachment.bin"
        content_type = clean_lobby_text(item.get("content_type"), limit=120) or "application/octet-stream"
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        cleaned.append(
            {
                "id": attachment_id,
                "filename": filename,
                "content_type": content_type,
                "size": max(0, size),
                "is_image": item.get("is_image") is True,
                "url": clean_lobby_text(item.get("url"), limit=200),
                "download_url": clean_lobby_text(item.get("download_url"), limit=200),
            }
        )
    return cleaned


def normalize_lobby_side(value: object) -> LobbySide:
    return value if value in LOBBY_SIDES else "other"  # type: ignore[return-value]


def normalize_lobby_kind(value: object) -> LobbyKind:
    return value if value in LOBBY_KINDS else "message"  # type: ignore[return-value]


def normalize_lobby_channel(value: object, default: Literal["lobby", "side_chat"] = "lobby") -> Literal["lobby", "side_chat"]:
    return value if value in LOBBY_CHANNELS else default  # type: ignore[return-value]


def normalize_actor_type(value: object, *, actor_id: object = None, side: object = None) -> str:
    """Resolve who authored an event: a person or an agent.

    An explicit caller value wins (identity-layer endpoints stamp it from the
    verified session). Otherwise fall back to the historical inference: events
    without an actor_id come from the host-browser human path; events with one
    come from agent endpoints.
    """
    cleaned = str(value or "").strip().lower()
    if cleaned in {"human", "agent"}:
        return cleaned
    if str(actor_id or "").strip():
        return "agent"
    return "human" if str(side or "") in {"", "mine", "other"} else "agent"


def normalize_chain_depth(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    if isinstance(value, float) and value.is_integer():
        return max(0, int(value))
    if isinstance(value, str) and value.strip().isdigit():
        return max(0, int(value.strip()))
    return 0


def normalize_flow_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    if isinstance(value, float) and value.is_integer():
        return max(0, int(value))
    if isinstance(value, str) and value.strip().isdigit():
        return max(0, int(value.strip()))
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
