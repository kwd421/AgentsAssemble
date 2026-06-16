"""Custom room channels (Discord-style): user-created text/voice channels.

These live *alongside* the four fixed functional surfaces (lobby/live/board/
records), which are untouched. The registry holds only the channels a room
creates: each text channel gets its own message stream (channel_<id>.jsonl,
read/written through the generic lobby event reader); each voice channel is a
presence entity (who is connected) with real audio streaming deferred.

Channels persist in room_settings.json under "channels" as an ordered list.
This module is the single source of truth for id/name/type normalization, the
default (empty) list, and the create/rename/delete/reorder mutations — all pure
functions over a channel list, so the HTTP layer stays thin and tests are direct.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

CHANNEL_TYPES = {"text", "voice"}
CHANNEL_NAME_LIMIT = 60
MAX_CHANNELS_PER_ROOM = 50

# Custom channel ids are opaque + filesystem/url/stream safe ([a-z0-9]). A name
# is a separate, freely-editable display label (rename never moves the stream).
_CHANNEL_ID_RE = r"c[0-9a-f]{12}"


class ChannelError(ValueError):
    """A channel mutation the caller asked for can't be satisfied (bad type,
    unknown id, name clash, room full). Carries a category for the HTTP layer."""

    def __init__(self, message: str, *, category: str = "invalid") -> None:
        super().__init__(message)
        self.category = category


def clean_channel_type(value: object) -> str:
    kind = str(value or "").strip().lower()
    return kind if kind in CHANNEL_TYPES else "text"


def clean_channel_name(value: object) -> str:
    """Single-line, length-bounded display name. Channel names may be any script
    (한글 포함) — only control whitespace is collapsed; emptiness is the caller's
    concern (create rejects empty; normalization of stored data keeps a blank)."""
    text = str(value or "").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return " ".join(text.split())[:CHANNEL_NAME_LIMIT]


def _new_channel_id() -> str:
    return "c" + uuid4().hex[:12]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def clean_channel(value: object) -> dict[str, object] | None:
    """Normalize one stored entry to the public shape, or None if unusable
    (missing id/name). Position is coerced to int; unknown keys are dropped."""
    source = value if isinstance(value, dict) else {}
    channel_id = str(source.get("id") or "").strip()
    name = clean_channel_name(source.get("name"))
    if not channel_id or not name:
        return None
    try:
        position = int(source.get("position") or 0)
    except (TypeError, ValueError):
        position = 0
    created_at = str(source.get("created_at") or "")[:64]
    return {
        "id": channel_id,
        "name": name,
        "type": clean_channel_type(source.get("type")),
        "position": max(0, position),
        "created_at": created_at,
    }


def clean_channels(value: object) -> list[dict[str, object]]:
    """Normalize a stored channel list: drop bad entries, de-dup ids (first
    wins), clamp to MAX, and return ordered by (position, created_at) so the
    list is stable regardless of how it was persisted."""
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    channels: list[dict[str, object]] = []
    for raw in value:
        channel = clean_channel(raw)
        if channel is None or channel["id"] in seen:
            continue
        seen.add(str(channel["id"]))
        channels.append(channel)
        if len(channels) >= MAX_CHANNELS_PER_ROOM:
            break
    channels.sort(key=lambda item: (int(item["position"]), str(item["created_at"]), str(item["id"])))
    return _renumber(channels)


def _renumber(channels: list[dict[str, object]]) -> list[dict[str, object]]:
    """Re-pack positions to 0..n-1 in current order, keeping the list dense."""
    for index, channel in enumerate(channels):
        channel["position"] = index
    return channels


def find_channel(channels: list[dict[str, object]], channel_id: str) -> dict[str, object] | None:
    target = str(channel_id or "").strip()
    for channel in channels:
        if str(channel.get("id")) == target:
            return channel
    return None


def add_channel(
    channels: list[dict[str, object]],
    *,
    name: object,
    channel_type: object = "text",
    channel_id: str = "",
    created_at: str = "",
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Append a new channel; return (new_list, the_created_channel).

    Names need not be unique (Discord allows duplicates), but an empty name or a
    full room is rejected. `channel_id`/`created_at` are injectable for tests;
    by default a fresh opaque id + UTC timestamp are generated."""
    current = clean_channels(channels)
    if len(current) >= MAX_CHANNELS_PER_ROOM:
        raise ChannelError("channel limit reached for this room", category="limit")
    clean_name = clean_channel_name(name)
    if not clean_name:
        raise ChannelError("channel name is required", category="name")
    new_id = str(channel_id or "").strip() or _new_channel_id()
    if any(str(channel.get("id")) == new_id for channel in current):
        raise ChannelError("channel id already exists", category="duplicate")
    channel = {
        "id": new_id,
        "name": clean_name,
        "type": clean_channel_type(channel_type),
        "position": len(current),
        "created_at": str(created_at or "") or _now_iso(),
    }
    return clean_channels([*current, channel]), channel


def rename_channel(
    channels: list[dict[str, object]], channel_id: str, name: object
) -> list[dict[str, object]]:
    current = clean_channels(channels)
    clean_name = clean_channel_name(name)
    if not clean_name:
        raise ChannelError("channel name is required", category="name")
    target = str(channel_id or "").strip()
    if not any(str(channel.get("id")) == target for channel in current):
        raise ChannelError("unknown channel", category="not_found")
    for channel in current:
        if str(channel.get("id")) == target:
            channel["name"] = clean_name
    return current


def remove_channel(channels: list[dict[str, object]], channel_id: str) -> list[dict[str, object]]:
    current = clean_channels(channels)
    target = str(channel_id or "").strip()
    remaining = [channel for channel in current if str(channel.get("id")) != target]
    if len(remaining) == len(current):
        raise ChannelError("unknown channel", category="not_found")
    return clean_channels(remaining)


def reorder_channels(
    channels: list[dict[str, object]], ordered_ids: list[str]
) -> list[dict[str, object]]:
    """Apply a new order. Ids in `ordered_ids` lead in the given order; any
    channel not named keeps its relative order after them (no silent drops)."""
    current = clean_channels(channels)
    by_id = {str(channel["id"]): channel for channel in current}
    ordered: list[dict[str, object]] = []
    used: set[str] = set()
    for raw_id in ordered_ids or []:
        channel_id = str(raw_id or "").strip()
        if channel_id in by_id and channel_id not in used:
            ordered.append(by_id[channel_id])
            used.add(channel_id)
    for channel in current:
        if str(channel["id"]) not in used:
            ordered.append(channel)
    return _renumber(ordered)
