"""Ephemeral voice-channel presence: who is currently connected to a voice
channel. CH-4 delivers the *entity* — joins/leaves/roster — while real audio
streaming (WebRTC + TURN) is deferred. A voice channel is a place participants
are "in", shown live, before a single audio packet flows.

Presence is heartbeat-refreshed with a TTL (like the typing registry in
room_members): a client re-posts join every ~20s; a dropped client falls out
after the TTL so a ghost never lingers in the voice roster. Nothing here is
persisted — it's live state, rebuilt from heartbeats, not from disk.
"""
from __future__ import annotations

import threading
import time

# 45s tolerates ~2 missed 20s heartbeats before a silent dropout clears.
_VOICE_TTL_SECONDS = 45.0
_voice_lock = threading.Lock()
# (meeting_id, channel_id, participant_id) -> {"expiry", "name", "muted"}
_voice: dict[tuple[str, str, str], dict[str, object]] = {}


def join_voice(
    meeting_id: str,
    channel_id: str,
    participant_id: str,
    *,
    display_name: str = "",
    self_muted: bool = False,
    now: float | None = None,
) -> None:
    """Join (or heartbeat) a voice channel; refreshes the TTL each call."""
    moment = time.monotonic() if now is None else now
    key = (str(meeting_id or ""), str(channel_id or ""), str(participant_id or ""))
    if not all(key):
        return
    with _voice_lock:
        _voice[key] = {
            "expiry": moment + _VOICE_TTL_SECONDS,
            "name": str(display_name or participant_id),
            "muted": bool(self_muted),
        }


def leave_voice(meeting_id: str, channel_id: str, participant_id: str) -> None:
    key = (str(meeting_id or ""), str(channel_id or ""), str(participant_id or ""))
    with _voice_lock:
        _voice.pop(key, None)


def leave_all_voice(meeting_id: str, participant_id: str) -> None:
    """Drop a participant from every voice channel in a room (e.g. on kick)."""
    target_meeting = str(meeting_id or "")
    target_participant = str(participant_id or "")
    with _voice_lock:
        for key in [
            key for key in _voice
            if key[0] == target_meeting and key[2] == target_participant
        ]:
            _voice.pop(key, None)


def _prune(moment: float) -> None:
    for key in [key for key, value in _voice.items() if moment > float(value["expiry"])]:
        _voice.pop(key, None)


def voice_participants(
    meeting_id: str, channel_id: str, *, now: float | None = None
) -> list[dict[str, object]]:
    """Active participants in one voice channel, sorted by name (stable roster)."""
    moment = time.monotonic() if now is None else now
    target = (str(meeting_id or ""), str(channel_id or ""))
    with _voice_lock:
        _prune(moment)
        members = [
            {"participant_id": pid, "name": str(value["name"]), "muted": bool(value["muted"])}
            for (mid, cid, pid), value in _voice.items()
            if (mid, cid) == target
        ]
    members.sort(key=lambda member: (str(member["name"]).casefold(), str(member["participant_id"])))
    return members


def voice_presence_for_room(meeting_id: str, *, now: float | None = None) -> dict[str, list[dict[str, object]]]:
    """All voice channels in a room → their active participants. Channels with
    nobody connected are omitted (the caller overlays the channel registry)."""
    moment = time.monotonic() if now is None else now
    target_meeting = str(meeting_id or "")
    presence: dict[str, list[dict[str, object]]] = {}
    with _voice_lock:
        _prune(moment)
        for (mid, cid, pid), value in _voice.items():
            if mid != target_meeting:
                continue
            presence.setdefault(cid, []).append(
                {"participant_id": pid, "name": str(value["name"]), "muted": bool(value["muted"])}
            )
    for members in presence.values():
        members.sort(key=lambda member: (str(member["name"]).casefold(), str(member["participant_id"])))
    return presence


def reset_voice_presence() -> None:
    """Test hook: clear all voice presence."""
    with _voice_lock:
        _voice.clear()
