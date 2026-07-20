"""Read-only queries over the legacy append-only lobby event log."""

from __future__ import annotations

from pathlib import Path

from agentsassemble.legacy.meeting.core.events import (
    clean_lobby_text,
    iter_lobby_events_newest_first,
    read_lobby_events,
)


LOBBY_HISTORY_PAGE_LIMIT = 50
LOBBY_HISTORY_MAX_PAGE_LIMIT = 200


def read_lobby(
    output_root: Path,
    limit: int | None = 80,
    *,
    meeting_id: str = "",
) -> list[dict[str, object]]:
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    if not clean_meeting_id:
        return read_lobby_events(output_root / "lobby.jsonl", limit=limit)

    # Filter while scanning backwards. Taking a global tail first can hide a
    # quiet room once unrelated rooms have appended enough newer events.
    cap = limit if isinstance(limit, int) and limit > 0 else None
    collected: list[dict[str, object]] = []
    for event in iter_lobby_events_newest_first(output_root / "lobby.jsonl"):
        if clean_lobby_text(event.get("flow_meeting_id"), limit=128) != clean_meeting_id:
            continue
        collected.append(event)
        if cap is not None and len(collected) >= cap:
            break
    collected.reverse()
    return collected


def read_lobby_before(
    output_root: Path,
    *,
    before_event_id: str,
    limit: int = LOBBY_HISTORY_PAGE_LIMIT,
    meeting_id: str = "",
) -> dict[str, object]:
    """Return one newest-last page strictly older than ``before_event_id``."""

    clean_limit = max(1, min(int(limit or LOBBY_HISTORY_PAGE_LIMIT), LOBBY_HISTORY_MAX_PAGE_LIMIT))
    clean_meeting_id = clean_lobby_text(meeting_id, limit=128)
    anchor = clean_lobby_text(before_event_id, limit=128)
    events: list[dict[str, object]] = []
    has_more = False
    seen_anchor = not anchor
    for event in iter_lobby_events_newest_first(output_root / "lobby.jsonl"):
        if not seen_anchor:
            if str(event.get("id") or "") == anchor:
                seen_anchor = True
            continue
        if clean_meeting_id and clean_lobby_text(event.get("flow_meeting_id"), limit=128) != clean_meeting_id:
            continue
        if len(events) >= clean_limit:
            has_more = True
            break
        events.append(event)
    events.reverse()
    return {"events": events, "has_more": has_more}
