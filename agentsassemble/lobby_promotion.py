from __future__ import annotations

import re
import threading
from pathlib import Path

from agentsassemble.live_agent_operations import append_live_agent_operation, redact_sensitive_operation_text
from agentsassemble.legacy.meeting.core.events import append_live_event, clean_lobby_text, read_live_events, read_lobby_events


MAX_LOBBY_PROMOTION_EVENT_IDS = 20
LOBBY_PROMOTION_OPERATION = "lobby.promote_to_official"

_PROMOTION_LOCK = threading.Lock()
_MEETING_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def promote_lobby_events_to_official(
    output_root: Path,
    meeting_id: str,
    lobby_event_ids: list[object],
    *,
    reason: str = "",
) -> dict[str, object]:
    clean_meeting_id = _clean_meeting_id(meeting_id)
    meeting_dir = output_root / "meetings" / clean_meeting_id
    if not meeting_dir.exists():
        raise ValueError(f"Meeting {clean_meeting_id} was not found.")
    requested_ids = _requested_lobby_event_ids(lobby_event_ids)
    safe_reason = _safe_promoted_text(reason, limit=240)
    with _PROMOTION_LOCK:
        lobby_events = _lobby_events_by_id(output_root)
        live_events = read_live_events(meeting_dir, limit=None)
        already_promoted = _already_promoted_source_ids(live_events)
        promotable_events: list[tuple[str, dict[str, object], str]] = []
        for source_event_id in requested_ids:
            if source_event_id in already_promoted:
                raise ValueError(f"Lobby event {source_event_id} was already promoted.")
            lobby_event = lobby_events.get(source_event_id)
            if lobby_event is None:
                raise ValueError(f"Lobby event id not found: {source_event_id}.")
            content = _safe_promoted_text(lobby_event.get("message"), limit=4000)
            if not content:
                raise ValueError(f"Lobby event {source_event_id} has no promotable text.")
            promotable_events.append((source_event_id, lobby_event, content))
        promoted_events: list[dict[str, object]] = []
        for source_event_id, lobby_event, content in promotable_events:
            promoted_events.append(
                append_live_event(
                    meeting_dir,
                    {
                        "kind": "promoted_context",
                        "meeting_id": clean_meeting_id,
                        "channel": "official",
                        "official_record": True,
                        "actor_id": "moderator",
                        "source_event_id": source_event_id,
                        "display_name": _promoted_display_name(lobby_event),
                        "engagement_mode": "promoted_from_lobby",
                        "promoted_from": "lobby",
                        "promoted_from_actor_id": clean_lobby_text(lobby_event.get("actor_id"), limit=64),
                        "promoted_reason": safe_reason,
                        "content": content,
                    },
                )
            )
        operation = append_live_agent_operation(
            output_root,
            operation=LOBBY_PROMOTION_OPERATION,
            status="success",
            target_id=clean_meeting_id,
            summary=f"Promoted {len(promoted_events)} lobby event(s) into official meeting context.",
            details={
                "source_event_ids": requested_ids,
                "promoted_event_ids": [
                    str(event.get("id") or "") for event in promoted_events if str(event.get("id") or "").strip()
                ],
                "promoted_count": len(promoted_events),
            },
        )
    return {
        "status": "promoted",
        "meeting_id": clean_meeting_id,
        "source_event_ids": requested_ids,
        "promoted_event_ids": [
            str(event.get("id") or "") for event in promoted_events if str(event.get("id") or "").strip()
        ],
        "events": promoted_events,
        "operation": operation,
    }


def _clean_meeting_id(value: object) -> str:
    meeting_id = clean_lobby_text(value, limit=128)
    if not meeting_id or _MEETING_ID_PATTERN.fullmatch(meeting_id) is None:
        raise ValueError("Meeting id is required.")
    return meeting_id


def _requested_lobby_event_ids(values: list[object]) -> list[str]:
    event_ids = [clean_lobby_text(value, limit=128) for value in values]
    event_ids = [event_id for event_id in event_ids if event_id]
    if not event_ids:
        raise ValueError("At least one lobby event id is required.")
    if len(event_ids) > MAX_LOBBY_PROMOTION_EVENT_IDS:
        raise ValueError(f"Can promote at most {MAX_LOBBY_PROMOTION_EVENT_IDS} lobby events at once.")
    if len(set(event_ids)) != len(event_ids):
        raise ValueError("Lobby event ids must be distinct.")
    return event_ids


def _lobby_events_by_id(output_root: Path) -> dict[str, dict[str, object]]:
    events: dict[str, dict[str, object]] = {}
    for event in read_lobby_events(output_root / "lobby.jsonl", limit=None):
        if not isinstance(event, dict):
            continue
        if str(event.get("channel") or "lobby") != "lobby":
            continue
        event_id = clean_lobby_text(event.get("id"), limit=128)
        if event_id:
            events[event_id] = event
    return events


def _already_promoted_source_ids(live_events: list[dict[str, object]]) -> set[str]:
    return {
        source_event_id
        for event in live_events
        if str(event.get("kind") or "") == "promoted_context"
        and (source_event_id := clean_lobby_text(event.get("source_event_id"), limit=128))
    }


def _promoted_display_name(lobby_event: dict[str, object]) -> str:
    source_name = clean_lobby_text(lobby_event.get("name") or lobby_event.get("actor_id") or "Lobby", limit=64)
    return f"{source_name or 'Lobby'} (promoted from lobby)"


def _safe_promoted_text(value: object, *, limit: int) -> str:
    return clean_lobby_text(redact_sensitive_operation_text(str(value or "")), limit=limit)
