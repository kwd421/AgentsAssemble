"""Server-governed room speech helpers.

This module owns the shared lobby-say policy core for participant speech. Route
modules still decide how to parse requests and report transport-specific errors;
the rules that decide who is speaking and what identity reaches the room log
live here.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path


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
AllowsRoomScope = Callable[[dict[str, object]], bool]
IsMuted = Callable[[Path, str, str], bool]


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
) -> dict[str, object]:
    """Stamp authenticated identity and append one lobby speech event.

    Client-supplied identity fields are never trusted. The caller may set
    `policy_already_checked` only when it called `ensure_lobby_say_allowed`
    immediately before parsing transport-specific payload.
    """
    if not policy_already_checked:
        ensure_lobby_say_allowed(output_root, identity, is_muted=is_muted)
    event_payload = dict(payload)
    if require_nonempty_message and not str(event_payload.get("message") or "").strip():
        raise GovernedLobbySayRejected("Message is required.", category="empty")
    event_payload["name"] = identity.display_name
    event_payload["actor_id"] = identity.agent_id
    event_payload["actor_type"] = identity.actor_type
    event_payload["side"] = "other"
    requested_kind = str(event_payload.get("kind") or "")
    event_payload["kind"] = requested_kind if requested_kind in {"vote", "vote_cast"} else "message"
    if identity.meeting_id:
        event_payload["flow_meeting_id"] = identity.meeting_id
    return append_lobby_event(
        output_root,
        event_payload,
        allow_flow_metadata=public_lobby_allows_room_scope(event_payload),
    )

