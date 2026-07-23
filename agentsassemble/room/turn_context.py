from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agentsassemble.room.text import clean_room_text as clean_lobby_text
from agentsassemble.providers.turn_input import agent_turn_prompt
from agentsassemble.room.context import (
    DEFAULT_ROOM_CONTEXT_CHARS,
    DEFAULT_ROOM_CONTEXT_MESSAGES,
    project_room_context,
)
from agentsassemble.room.repository import RoomRepository
from agentsassemble.providers.sync_cursor import canonical_provider_sync_seq
from agentsassemble.persistence.local.room.repository import RoomStore

DEFAULT_ROOM_TURN_MAX_RECENT_EVENTS = DEFAULT_ROOM_CONTEXT_MESSAGES
DEFAULT_ROOM_TURN_MAX_PROMPT_CHARS = 20000
UNSUPPORTED_MEDIA_AUDIT_NOTE = "Unsupported media is listed for audit only; do not claim you viewed unsupported files."
MODEL_VISIBLE_MEDIA_REPRESENTATIONS = {"native", "text", "metadata", "unsupported"}
MAX_MODEL_VISIBLE_MEDIA_SIZE = 1_000_000_000_000


@dataclass(frozen=True)
class BoundedProviderContext:
    provider_input: str
    events: tuple[dict[str, object], ...]
    media_manifest: tuple[dict[str, object], ...]
    room_memory_included: bool


def _agent_turn_prompt(packet: dict[str, object]) -> str:
    return agent_turn_prompt(packet)


def build_room_turn_packet(
    output_root: Path,
    *,
    room_id: str,
    participant_id: str,
    session_id: str,
    instruction: str,
    media_ids: object = None,
    max_recent_events: object = None,
    max_prompt_chars: object = None,
    include_instruction: bool = True,
    repository: RoomRepository | None = None,
) -> dict[str, object]:
    clean_instruction = clean_lobby_text(instruction, limit=2000)
    if not clean_instruction:
        raise ValueError("Agent Session turn instruction is required")
    store = repository or RoomStore(output_root)
    session = store.session(room_id, session_id)
    participant = store.participant(room_id, participant_id)
    room = store.room(room_id)
    room_identity = {
        "room_name": clean_lobby_text(room.get("label") or room_id, limit=128),
        "display_name": clean_lobby_text(
            session.get("display_name") or participant.get("display_name") or participant_id,
            limit=80,
        ),
        "participant_id": participant_id,
    }
    last_seen_event_id = clean_lobby_text(session.get("last_seen_event_id"), limit=128)
    last_provider_sync_event_id = clean_lobby_text(session.get("last_provider_sync_event_id"), limit=128)
    last_seen_seq = _nonnegative_int(session.get("last_seen_seq")) or store.event_sequence(room_id, last_seen_event_id)
    last_provider_sync_seq = canonical_provider_sync_seq(
        store,
        room_id,
        participant_id,
        session,
    )
    recent_limit = _positive_int(max_recent_events, DEFAULT_ROOM_TURN_MAX_RECENT_EVENTS)
    prompt_limit = _positive_int(max_prompt_chars, DEFAULT_ROOM_TURN_MAX_PROMPT_CHARS)
    bootstrap_done = bool(session.get("bootstrap_done"))
    recovery_required = bool(session.get("recovery_required"))
    context_after_seq = 0 if not bootstrap_done else last_provider_sync_seq
    provider_projection = project_room_context(
        store,
        room_id=room_id,
        participant_id=participant_id,
        after_seq=context_after_seq,
        max_messages=recent_limit,
        max_chars=min(DEFAULT_ROOM_CONTEXT_CHARS, max(256, prompt_limit // 2)),
    )
    provider_events = list(provider_projection.events)
    media_events = store.read_events(
        room_id,
        event_types=("media_attached", "unsupported_media"),
    )
    media_manifest = _selected_media_manifest(
        media_events,
        media_ids=media_ids,
        room_delta_text=provider_projection.text,
    )
    unsupported_media = [media for media in media_manifest if not bool(media.get("supported"))]
    room_memory = room_memory_from_session(session)
    summary = dict(room_memory)
    input_mode = "recovery" if recovery_required else "bootstrap" if not bootstrap_done else "delta"
    bounded_context = _fit_provider_context(
        input_mode=input_mode,
        instruction=clean_instruction if include_instruction else "",
        room_identity=room_identity,
        room_memory=room_memory,
        events=provider_events,
        omitted_event_count=provider_projection.omitted_message_count,
        media_manifest=media_manifest,
        media_selection_is_explicit=bool(_clean_text_list(media_ids, limit=128)),
        prompt_limit=prompt_limit,
    )
    provider_input = bounded_context.provider_input
    provider_events = list(bounded_context.events)
    media_manifest = list(bounded_context.media_manifest)
    unsupported_media = [media for media in media_manifest if not bool(media.get("supported"))]
    room_delta_included = bool(provider_events)
    if input_mode == "delta" and not room_delta_included:
        input_mode = "current_only"
    bootstrap_included = input_mode == "bootstrap"
    recovery_summary_included = bounded_context.room_memory_included
    latest_event = provider_events[-1] if provider_events else {}
    latest_delta_event_id = clean_lobby_text(latest_event.get("id"), limit=128)
    latest_delta_seq = _nonnegative_int(latest_event.get("seq")) or last_provider_sync_seq
    packet = {
        "room_id": room_id,
        "participant_id": participant_id,
        "session_id": session_id,
        "summary": summary,
        "include_summary": bool(recovery_summary_included),
        "summary_checkpoint_id": clean_lobby_text(summary.get("up_to_event_id") if isinstance(summary, dict) else "", limit=128),
        "after_event_id": last_seen_event_id,
        "after_seq": last_seen_seq,
        "events": provider_events,
        "provider_input": provider_input,
        "input_mode": input_mode,
        "provider_visible_chars": len(provider_input),
        "provider_visible_event_count": len(provider_events),
        "filtered_internal_event_count": provider_projection.filtered_internal_event_count,
        "filtered_message_delta_count": provider_projection.filtered_message_delta_count,
        "provider_context_after_seq": context_after_seq,
        "last_provider_sync_event_id_before": last_provider_sync_event_id,
        "last_provider_sync_event_id_after": latest_delta_event_id or last_provider_sync_event_id,
        "last_provider_sync_seq_before": last_provider_sync_seq,
        "last_provider_sync_seq_after": latest_delta_seq,
        "bootstrap_cutoff_seq": _nonnegative_int(session.get("bootstrap_cutoff_seq")),
        "bootstrap_included": bootstrap_included,
        "room_delta_included": room_delta_included,
        "recovery_summary_included": recovery_summary_included,
        "recent_event_count": len(provider_events),
        "max_recent_events": recent_limit,
        "max_prompt_chars": prompt_limit,
        "media_manifest": media_manifest,
        "media_supported_count": len([media for media in media_manifest if bool(media.get("supported"))]),
        "media_unsupported_count": len(unsupported_media),
        "media_notes": [UNSUPPORTED_MEDIA_AUDIT_NOTE] if unsupported_media else [],
        "current_turn_instruction": clean_instruction,
        "settings": {
            "model": session.get("model", ""),
            "effort": session.get("effort", ""),
            "sandbox": session.get("sandbox", ""),
            "permissions": session.get("permissions", ""),
        },
        "explicit_non_goals": [
            "Do not inspect or edit the project unless the room conversation explicitly asks for it.",
            "Do not access credentials, secret environment variables, or unrelated local files.",
        ],
    }
    return _bound_room_turn_packet(packet, prompt_limit)


def build_provider_bootstrap_input(
    *,
    instruction: str,
    room_memory: dict[str, object] | None = None,
    room_delta: str = "",
    media_manifest: list[dict[str, object]] | None = None,
    unsupported_media: list[dict[str, object]] | None = None,
    room_identity: dict[str, object] | None = None,
) -> str:
    parts = [
        "[Agent Session bootstrap]",
        "You are participating in a shared AgentsAssemble room.",
        "Do not inspect or edit the project unless the current room instruction explicitly asks for it.",
        "Do not reveal internal runtime data, process ids, tokens, or hidden chain-of-thought.",
    ]
    identity_text = _provider_room_identity_text(room_identity or {})
    if identity_text:
        parts.extend(["", "[Your room identity]", identity_text])
    memory_text = _room_memory_text(room_memory or {})
    if memory_text:
        parts.extend(["", "[Room memory]", memory_text])
    if room_delta:
        parts.extend(["", "[Room update since your last sync]", room_delta])
    media_text = _provider_media_text(media_manifest or [], unsupported_media or [])
    if media_text:
        parts.extend(["", media_text])
    clean_instruction = clean_lobby_text(instruction, limit=2000)
    if clean_instruction:
        parts.extend(["", "[Your turn]", clean_instruction])
    return "\n".join(parts).strip() + "\n"


def build_provider_turn_input(
    *,
    instruction: str,
    room_delta: str = "",
    media_manifest: list[dict[str, object]] | None = None,
    unsupported_media: list[dict[str, object]] | None = None,
    room_identity: dict[str, object] | None = None,
) -> str:
    parts = []
    identity_text = _provider_room_identity_text(room_identity or {})
    if identity_text:
        parts.extend(["[Your room identity]", identity_text, ""])
    if room_delta:
        parts.extend(["[Room update since your last turn]", room_delta, ""])
    media_text = _provider_media_text(media_manifest or [], unsupported_media or [])
    if media_text:
        parts.extend([media_text, ""])
    clean_instruction = clean_lobby_text(instruction, limit=2000)
    if clean_instruction:
        parts.extend(["[Your turn]", clean_instruction])
    return "\n".join(parts).strip() + "\n"


def build_provider_recovery_input(
    *,
    instruction: str,
    room_memory: dict[str, object],
    room_delta: str = "",
    media_manifest: list[dict[str, object]] | None = None,
    unsupported_media: list[dict[str, object]] | None = None,
    room_identity: dict[str, object] | None = None,
) -> str:
    parts = [
        "[Agent Session recovery]",
        "This compact room memory was retained before the provider restart.",
    ]
    identity_text = _provider_room_identity_text(room_identity or {})
    if identity_text:
        parts.extend(["", "[Your room identity]", identity_text])
    memory_text = _room_memory_text(room_memory)
    if memory_text:
        parts.extend(["", "[Room memory]", memory_text])
    if room_delta:
        parts.extend(["", "[Room update since recovery point]", room_delta])
    media_text = _provider_media_text(media_manifest or [], unsupported_media or [])
    if media_text:
        parts.extend(["", media_text])
    clean_instruction = clean_lobby_text(instruction, limit=2000)
    if clean_instruction:
        parts.extend(["", "[Your turn]", clean_instruction])
    return "\n".join(parts).strip() + "\n"


def room_memory_from_session(session: dict[str, object]) -> dict[str, object]:
    memory = session.get("room_memory") if isinstance(session.get("room_memory"), dict) else {}
    legacy_summary = session.get("summary") if isinstance(session.get("summary"), dict) else {}
    return {
        "summary": clean_lobby_text(memory.get("summary") or legacy_summary.get("summary") or legacy_summary.get("text"), limit=4000),
        "decisions": _clean_text_list(memory.get("decisions") or legacy_summary.get("decisions"), limit=1200),
        "open_questions": _clean_text_list(memory.get("open_questions") or legacy_summary.get("open_questions"), limit=1200),
        "up_to_event_id": clean_lobby_text(memory.get("up_to_event_id") or legacy_summary.get("up_to_event_id"), limit=128),
        "compacted_at": clean_lobby_text(memory.get("compacted_at") or legacy_summary.get("compacted_at"), limit=128),
    }


def _provider_room_identity_text(identity: dict[str, object]) -> str:
    display_name = clean_lobby_text(identity.get("display_name"), limit=80)
    room_name = clean_lobby_text(identity.get("room_name"), limit=128)
    if not display_name and not room_name:
        return ""
    lines = []
    if display_name:
        lines.append(f"Your display name in this room is: {display_name}")
    if room_name:
        lines.append(f"The room name is: {room_name}")
    lines.append("Use the display name above when someone asks who you are in this room.")
    return "\n".join(lines)


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _selected_media_manifest(
    events: list[dict[str, object]],
    *,
    media_ids: object = None,
    room_delta_text: str = "",
) -> list[dict[str, object]]:
    manifest_by_id: dict[str, dict[str, object]] = {}
    ordered: list[dict[str, object]] = []
    for event in events:
        media = event.get("media")
        if not isinstance(media, dict):
            continue
        media_id = clean_lobby_text(media.get("id"), limit=128)
        if not media_id:
            continue
        item = _model_visible_media(media, media_id=media_id)
        manifest_by_id[media_id] = item
        ordered.append(item)
    selected_ids = _clean_text_list(media_ids, limit=128)
    if selected_ids:
        return [manifest_by_id[media_id] for media_id in selected_ids if media_id in manifest_by_id]
    referenced_text = clean_lobby_text(room_delta_text, limit=4000).lower()
    if not referenced_text:
        return []
    referenced = []
    for media in ordered:
        media_id = clean_lobby_text(media.get("id"), limit=128).lower()
        filename = clean_lobby_text(media.get("filename"), limit=256).lower()
        if (media_id and media_id in referenced_text) or (filename and filename in referenced_text):
            referenced.append(media)
    return referenced


def _room_memory_text(memory: dict[str, object]) -> str:
    parts: list[str] = []
    summary = clean_lobby_text(memory.get("summary"), limit=4000)
    if summary:
        parts.append(f"Summary: {summary}")
    decisions = _clean_text_list(memory.get("decisions"), limit=1200)
    if decisions:
        parts.append("Decisions: " + "; ".join(decisions))
    questions = _clean_text_list(memory.get("open_questions"), limit=1200)
    if questions:
        parts.append("Open questions: " + "; ".join(questions))
    return "\n".join(parts)


def _provider_media_text(media_manifest: list[dict[str, object]], unsupported_media: list[dict[str, object]]) -> str:
    if not media_manifest:
        return ""
    lines = ["[Current turn media]"]
    for media in media_manifest[:10]:
        media_id = clean_lobby_text(media.get("id"), limit=128)
        filename = clean_lobby_text(media.get("filename"), limit=200)
        supported = "supported" if bool(media.get("supported")) else "unsupported"
        lines.append(f"- {filename or media_id}: {supported}")
    if unsupported_media:
        lines.append(UNSUPPORTED_MEDIA_AUDIT_NOTE)
    return "\n".join(lines)


def _clean_text_list(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned = []
    for item in value:
        text = clean_lobby_text(item, limit=limit)
        if text:
            cleaned.append(text)
    return cleaned


def _bound_room_turn_packet(packet: dict[str, object], prompt_limit: int) -> dict[str, object]:
    provider_input = str(packet.get("provider_input") or "")
    if len(provider_input) > prompt_limit:
        raise ValueError("provider input exceeds max_prompt_chars after structural budgeting")
    events = list(packet.get("events") if isinstance(packet.get("events"), list) else [])
    return {
        **packet,
        "provider_input": provider_input,
        "provider_visible_chars": len(provider_input),
        "events": events,
        "event_count_in_packet": len(events),
    }


def _fit_provider_context(
    *,
    input_mode: str,
    instruction: str,
    room_identity: dict[str, object],
    room_memory: dict[str, object],
    events: list[dict[str, object]],
    omitted_event_count: int,
    media_manifest: list[dict[str, object]],
    media_selection_is_explicit: bool,
    prompt_limit: int,
) -> BoundedProviderContext:
    included_events = list(events)
    included_media = list(media_manifest)
    included_memory = dict(room_memory)

    def render() -> str:
        room_delta = _provider_events_text(included_events, omitted_event_count=omitted_event_count)
        unsupported = [media for media in included_media if not bool(media.get("supported"))]
        if input_mode == "recovery":
            return build_provider_recovery_input(
                instruction=instruction,
                room_identity=room_identity,
                room_memory=included_memory,
                room_delta=room_delta,
                media_manifest=included_media,
                unsupported_media=unsupported,
            )
        if input_mode == "bootstrap":
            return build_provider_bootstrap_input(
                instruction=instruction,
                room_identity=room_identity,
                room_memory=included_memory,
                room_delta=room_delta,
                media_manifest=included_media,
                unsupported_media=unsupported,
            )
        return build_provider_turn_input(
            instruction=instruction,
            room_identity=room_identity,
            room_delta=room_delta,
            media_manifest=included_media,
            unsupported_media=unsupported,
        )

    provider_input = render()
    if len(provider_input) > prompt_limit and _room_memory_text(included_memory):
        included_memory = {}
        provider_input = render()
    while included_events and len(provider_input) > prompt_limit:
        included_events.pop()
        if not media_selection_is_explicit:
            included_media = _media_referenced_by_events(included_media, included_events)
        provider_input = render()
    if len(provider_input) > prompt_limit and included_media:
        included_media = []
        provider_input = render()
    if len(provider_input) > prompt_limit:
        provider_input = _fit_required_provider_input(
            input_mode=input_mode,
            instruction=instruction,
            room_identity=room_identity,
            prompt_limit=prompt_limit,
        )
    return BoundedProviderContext(
        provider_input=provider_input,
        events=tuple(included_events),
        media_manifest=tuple(included_media),
        room_memory_included=bool(_room_memory_text(included_memory)),
    )


def _fit_required_provider_input(
    *,
    input_mode: str,
    instruction: str,
    room_identity: dict[str, object],
    prompt_limit: int,
) -> str:
    clean_instruction = clean_lobby_text(instruction, limit=2000)

    def render(required_instruction: str, identity: dict[str, object]) -> str:
        if input_mode == "recovery":
            return build_provider_recovery_input(
                instruction=required_instruction,
                room_identity=identity,
                room_memory={},
            )
        if input_mode == "bootstrap":
            return build_provider_bootstrap_input(
                instruction=required_instruction,
                room_identity=identity,
                room_memory={},
            )
        return build_provider_turn_input(
            instruction=required_instruction,
            room_identity=identity,
        )

    identity_candidates = [
        room_identity,
        {**room_identity, "participant_id": ""},
        {
            "display_name": clean_lobby_text(room_identity.get("display_name"), limit=40),
            "room_name": clean_lobby_text(room_identity.get("room_name"), limit=64),
            "participant_id": "",
        },
    ]
    for identity in identity_candidates:
        candidate = render(clean_instruction, identity)
        if len(candidate) <= prompt_limit:
            return candidate

    low = 1
    high = len(clean_instruction)
    best = ""
    compact_identity = identity_candidates[-1]
    while low <= high:
        midpoint = (low + high) // 2
        candidate = render(clean_instruction[:midpoint].rstrip(), compact_identity)
        if len(candidate) <= prompt_limit:
            best = candidate
            low = midpoint + 1
        else:
            high = midpoint - 1
    if not best:
        raise ValueError("max_prompt_chars is too small for required provider context")
    return best


def _model_visible_media(media: dict[str, object], *, media_id: str) -> dict[str, object]:
    filename_value = media.get("filename")
    filename = ""
    if isinstance(filename_value, str):
        filename = clean_lobby_text(filename_value, limit=256).replace("\\", "/").rsplit("/", 1)[-1]
    content_type_value = media.get("content_type")
    content_type = clean_lobby_text(content_type_value, limit=128) if isinstance(content_type_value, str) else ""
    if not re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", content_type):
        content_type = "application/octet-stream"
    size_value = media.get("size")
    try:
        size = int(size_value) if not isinstance(size_value, bool) else 0
    except (TypeError, ValueError, OverflowError):
        size = 0
    item: dict[str, object] = {
        "id": media_id,
        "filename": filename or media_id,
        "content_type": content_type,
        "size": min(MAX_MODEL_VISIBLE_MEDIA_SIZE, max(0, size)),
        "supported": media.get("supported") is True,
    }
    representation = media.get("representation")
    if isinstance(representation, str):
        clean_representation = clean_lobby_text(representation, limit=32).lower()
        if clean_representation in MODEL_VISIBLE_MEDIA_REPRESENTATIONS:
            item["representation"] = clean_representation
    return item


def _media_referenced_by_events(
    media_manifest: list[dict[str, object]],
    events: list[dict[str, object]],
) -> list[dict[str, object]]:
    event_text = "\n".join(clean_lobby_text(event.get("content"), limit=4000) for event in events).lower()
    return [
        media
        for media in media_manifest
        if (
            clean_lobby_text(media.get("id"), limit=128).lower() in event_text
            or clean_lobby_text(media.get("filename"), limit=256).lower() in event_text
        )
    ]


def _provider_events_text(events: list[dict[str, object]], *, omitted_event_count: int) -> str:
    lines: list[str] = []
    if omitted_event_count:
        lines.append(f"- [omitted {omitted_event_count} earlier room update(s)]")
    for event in events:
        speaker = clean_lobby_text(
            event.get("display_name") or event.get("participant_id") or event.get("actor_id"),
            limit=64,
        ) or "room"
        content = clean_lobby_text(event.get("content"), limit=4000)
        if content:
            lines.append(f"- {speaker}: {content}")
    return "\n".join(lines)


__all__ = [
    "BoundedProviderContext",
    "DEFAULT_ROOM_TURN_MAX_PROMPT_CHARS",
    "DEFAULT_ROOM_TURN_MAX_RECENT_EVENTS",
    "MAX_MODEL_VISIBLE_MEDIA_SIZE",
    "MODEL_VISIBLE_MEDIA_REPRESENTATIONS",
    "UNSUPPORTED_MEDIA_AUDIT_NOTE",
    "_agent_turn_prompt",
    "_bound_room_turn_packet",
    "_nonnegative_int",
    "build_provider_bootstrap_input",
    "build_provider_recovery_input",
    "build_provider_turn_input",
    "build_room_turn_packet",
    "room_memory_from_session",
]
