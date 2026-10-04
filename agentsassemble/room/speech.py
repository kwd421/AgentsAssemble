"""Server-governed room speech helpers.

This module owns the shared lobby-say policy core for participant speech. Route
modules still decide how to parse requests and report transport-specific errors;
the rules that decide who is speaking and what identity reaches the room log
live here.
"""
from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from time import monotonic


@dataclass(frozen=True)
class ActorIdentity:
    agent_id: str
    display_name: str
    participant_type: str = "human"
    invite_scope: str = "room"
    meeting_id: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ActorIdentity":
        agent_id = str(value.get("agent_id") or "")
        display_name = str(value.get("display_name") or agent_id)
        return cls(
            agent_id=agent_id,
            display_name=display_name,
            participant_type=str(value.get("participant_type") or "human"),
            invite_scope=str(value.get("invite_scope") or "room"),
            meeting_id=str(value.get("meeting_id") or ""),
        )

    @property
    def actor_type(self) -> str:
        return "human" if self.participant_type == "human" else "agent"


class GovernedLobbySayRejected(ValueError):
    """A lobby-say request failed server-side policy before append."""

    def __init__(self, message: str, *, category: str = "rejected") -> None:
        super().__init__(message)
        self.category = category


AppendLobbyEvent = Callable[..., dict[str, object]]
AppendLiveEvent = Callable[[Path, dict[str, object]], dict[str, object]]
AllowsRoomScope = Callable[[dict[str, object]], bool]
IsMuted = Callable[[Path, str, str], bool]
NowMonotonic = Callable[[], float]
PUBLIC_SPEECH_KINDS = frozenset({"vote", "vote_cast", "vote_withdraw", "vote_close"})
SERVER_AUTO_CHAIN_DEPTH_LIMIT = 8
SERVER_SPEECH_BURST_LIMIT = 20
SERVER_SPEECH_BURST_WINDOW_SECONDS = 10.0
_SPEECH_RATE_BUCKETS: dict[tuple[str, str, str], list[float]] = {}


def _chain_depth_value(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    if isinstance(value, float) and value.is_integer():
        return max(0, int(value))
    if isinstance(value, str) and value.strip().isdigit():
        return max(0, int(value.strip()))
    return 0


def _enforce_payload_limits(
    output_root: Path,
    identity: ActorIdentity,
    payload: Mapping[str, object],
    *,
    now_monotonic: NowMonotonic,
) -> None:
    chain_depth = _chain_depth_value(payload.get("auto_chain_depth"))
    if chain_depth > SERVER_AUTO_CHAIN_DEPTH_LIMIT:
        raise GovernedLobbySayRejected("auto-reply chain depth exceeded", category="chain_depth")
    now = float(now_monotonic())
    key = (str(output_root), identity.meeting_id, identity.agent_id)
    bucket = _SPEECH_RATE_BUCKETS.setdefault(key, [])
    window_start = now - SERVER_SPEECH_BURST_WINDOW_SECONDS
    bucket[:] = [timestamp for timestamp in bucket if timestamp > window_start]
    if len(bucket) >= SERVER_SPEECH_BURST_LIMIT:
        raise GovernedLobbySayRejected("speech rate limit exceeded", category="rate_limited")
    bucket.append(now)


def _stamped_room_speech_payload(
    *,
    identity: ActorIdentity,
    payload: Mapping[str, object],
    side: str,
    allowed_kinds: Collection[str] = PUBLIC_SPEECH_KINDS,
) -> dict[str, object]:
    event_payload = dict(payload)
    event_payload["name"] = identity.display_name
    event_payload["actor_id"] = identity.agent_id
    event_payload["actor_type"] = identity.actor_type
    event_payload["side"] = side
    requested_kind = str(event_payload.get("kind") or "")
    event_payload["kind"] = requested_kind if requested_kind in allowed_kinds else "message"
    if identity.meeting_id:
        event_payload["flow_meeting_id"] = identity.meeting_id
    return event_payload


def ensure_lobby_say_allowed(
    output_root: Path,
    identity: ActorIdentity,
    *,
    is_muted: IsMuted,
) -> None:
    if identity.invite_scope == "read_only":
        raise GovernedLobbySayRejected("This session cannot post.", category="read_only")
    if is_muted(output_root, identity.meeting_id, identity.agent_id):
        raise GovernedLobbySayRejected("muted by room host", category="muted")


def governed_lobby_say(
    output_root: Path,
    *,
    identity: ActorIdentity,
    payload: Mapping[str, object],
    append_lobby_event: AppendLobbyEvent,
    public_lobby_allows_room_scope: AllowsRoomScope,
    is_muted: IsMuted,
    require_nonempty_message: bool = False,
    policy_already_checked: bool = False,
    side: str = "other",
    live_agent_endpoint: bool = False,
    allow_flow_metadata: bool | None = None,
    allowed_kinds: Collection[str] = PUBLIC_SPEECH_KINDS,
    now_monotonic: NowMonotonic = monotonic,
) -> dict[str, object]:
    """Stamp authenticated identity and append one lobby speech event.

    Client-supplied identity fields are never trusted. The caller may set
    `policy_already_checked` only when it called `ensure_lobby_say_allowed`
    immediately before parsing transport-specific payload.
    """
    if not policy_already_checked:
        ensure_lobby_say_allowed(output_root, identity, is_muted=is_muted)
    event_payload = _stamped_room_speech_payload(
        identity=identity,
        payload=payload,
        side=side,
        allowed_kinds=allowed_kinds,
    )
    if require_nonempty_message and not str(event_payload.get("message") or "").strip():
        raise GovernedLobbySayRejected("Message is required.", category="empty")
    _enforce_payload_limits(output_root, identity, event_payload, now_monotonic=now_monotonic)
    if allow_flow_metadata is None:
        allow_flow_metadata = public_lobby_allows_room_scope(event_payload)
    append_kwargs: dict[str, object] = {"allow_flow_metadata": allow_flow_metadata}
    if live_agent_endpoint:
        append_kwargs["live_agent_endpoint"] = True
    return append_lobby_event(output_root, event_payload, **append_kwargs)


def governed_channel_say(
    output_root: Path,
    *,
    channel_path: Path,
    identity: ActorIdentity,
    payload: Mapping[str, object],
    append_channel_event: AppendLobbyEvent,
    is_muted: IsMuted,
    side: str = "other",
    require_nonempty_message: bool = False,
    policy_already_checked: bool = False,
    now_monotonic: NowMonotonic = monotonic,
) -> dict[str, object]:
    """Stamp authenticated identity and append one custom-channel speech event."""
    if not policy_already_checked:
        ensure_lobby_say_allowed(output_root, identity, is_muted=is_muted)
    event_payload = _stamped_room_speech_payload(
        identity=identity,
        payload=payload,
        side=side,
    )
    if require_nonempty_message and not str(event_payload.get("message") or "").strip():
        raise GovernedLobbySayRejected("Message is required.", category="empty")
    _enforce_payload_limits(output_root, identity, event_payload, now_monotonic=now_monotonic)
    return append_channel_event(channel_path, event_payload, allow_flow_metadata=True)


def governed_official_reply(
    meeting_dir: Path,
    *,
    identity: ActorIdentity,
    meeting_id: str,
    source_event_id: str,
    role_id: str,
    display_name: str,
    content: str,
    append_live_event: AppendLiveEvent,
    turn_id: str = "",
    turn_index: int | None = None,
    review_checkpoint_id: str = "",
) -> dict[str, object]:
    """Build and append one official-turn reply event."""
    event_payload: dict[str, object] = {
        "kind": "message",
        "meeting_id": meeting_id,
        "actor_id": identity.agent_id,
        "target_agent_id": identity.agent_id,
        "source_event_id": source_event_id,
        "role_id": role_id,
        "display_name": display_name or identity.display_name,
        "content": content,
        "turn_id": turn_id,
        "turn_index": turn_index,
        "engagement_mode": "moderator_called",
    }
    if review_checkpoint_id:
        event_payload.update(
            {
                "review_checkpoint_id": review_checkpoint_id,
                "channel": "review",
                "official_record": False,
            }
        )
    return append_live_event(meeting_dir, event_payload)
