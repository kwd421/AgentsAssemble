"""Legacy GUI lobby event persistence and request normalization."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from agentsassemble.identity.repository import IdentityBackend
from agentsassemble.legacy.meeting.core.events import (
    FLOW_METADATA_KEYS,
    append_lobby_event_to_file,
    clean_lobby_text,
)
from agentsassemble.room.attachments import normalize_attachment_references


_LOBBY_APPEND_LOCK = threading.RLock()


def append_lobby_event(
    output_root: Path,
    event: dict[str, object],
    *,
    live_agent_endpoint: bool = False,
    allow_flow_metadata: bool = False,
    identity_backend: IdentityBackend | None = None,
    identity_backend_factory: Callable[[Path], IdentityBackend],
) -> dict[str, object]:
    with _LOBBY_APPEND_LOCK:
        appended = append_lobby_event_to_file(
            output_root / "lobby.jsonl",
            event,
            live_agent_endpoint=live_agent_endpoint,
            allow_flow_metadata=allow_flow_metadata,
        )
    room_id = clean_lobby_text(appended.get("flow_meeting_id"), limit=128)
    if room_id:
        try:
            (identity_backend or identity_backend_factory(output_root)).touch_room(room_id)
        except Exception:
            # Legacy room discovery is best effort and must not reject a message.
            pass
    return appended


def lobby_payload_with_attachments(
    output_root: Path,
    payload: dict[str, object],
    *,
    list_meetings: Callable[[Path], list[dict[str, object]]],
) -> dict[str, object]:
    event = dict(payload)
    if "flow_meeting_id" in event:
        event["flow_meeting_id"] = clean_lobby_text(
            event.get("flow_meeting_id"),
            limit=128,
        )
    if not clean_lobby_text(event.get("flow_meeting_id"), limit=128):
        implicit_meeting_id = single_lobby_meeting_id(
            output_root,
            list_meetings=list_meetings,
        )
        if implicit_meeting_id:
            event["flow_meeting_id"] = implicit_meeting_id
    if "attachments" in event:
        event["attachments"] = normalize_attachment_references(
            output_root,
            event.get("attachments"),
        )
    return event


def single_lobby_meeting_id(
    output_root: Path,
    *,
    list_meetings: Callable[[Path], list[dict[str, object]]],
) -> str:
    meeting_ids = [
        clean_lobby_text(meeting.get("meeting_id"), limit=128)
        for meeting in list_meetings(output_root)
    ]
    meeting_ids = [meeting_id for meeting_id in meeting_ids if meeting_id]
    return meeting_ids[0] if len(meeting_ids) == 1 else ""


def public_lobby_allows_room_scope(payload: dict[str, object]) -> bool:
    if not clean_lobby_text(payload.get("flow_meeting_id"), limit=128):
        return False
    control_keys = FLOW_METADATA_KEYS - {"flow_meeting_id"}
    return not any(
        clean_lobby_text(payload.get(key), limit=128)
        for key in control_keys
    )
