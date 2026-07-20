"""Retained resident lobby and direct-message speech behavior."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentsassemble.live_agent_flow import FLOW_SPEAKING_ACTIONS
from agentsassemble.live_agents import heartbeat_live_agent, read_live_agents
from agentsassemble.legacy.live_agent.queries import require_live_agent
from agentsassemble.lobby_queries import read_lobby
from agentsassemble.legacy.meeting.core.events import LOBBY_KINDS, clean_lobby_text
from agentsassemble.features.social.direct_messages import append_live_agent_dm_reply
from agentsassemble.room.speech import (
    ActorIdentity,
    GovernedLobbySayRejected,
    ensure_lobby_say_allowed,
    governed_lobby_say,
)


LobbyAppender = Callable[..., dict[str, object]]
RoomScopePolicy = Callable[[dict[str, object]], bool]
MutedPolicy = Callable[..., bool]
SmokeRedactor = Callable[[Path, list[str]], dict[str, object]]
SmokeReplyProjection = Callable[[str, str], str]
SmokeSourcePolicy = Callable[[str], bool]


@dataclass(frozen=True)
class LegacyLiveAgentLobbySpeechDeps:
    append_lobby_event: LobbyAppender
    public_lobby_allows_room_scope: RoomScopePolicy
    is_muted: MutedPolicy
    lobby_lock: Any
    is_smoke_source_redacted: SmokeSourcePolicy
    redact_smoke_events: SmokeRedactor
    smoke_reply_message: SmokeReplyProjection
    smoke_reply_redaction: str


@dataclass(frozen=True)
class LegacyLiveAgentSpeechService:
    output_root: Path
    lobby: LegacyLiveAgentLobbySpeechDeps

    def post_dm_reply(self, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
        response = append_live_agent_dm_reply(self.output_root, agent_id, payload)
        source_event_id = clean_lobby_text(payload.get("source_event_id"), limit=128)
        heartbeat_live_agent(
            self.output_root,
            agent_id,
            status="online",
            metadata={"last_observed_dm_event_id": source_event_id, "last_error": ""},
        )
        response["agent"] = require_live_agent(self.output_root, agent_id)
        response["agents"] = read_live_agents(self.output_root)
        return response

    def post_lobby_message(self, agent_id: str, payload: dict[str, object]) -> dict[str, object]:
        agent = heartbeat_live_agent(self.output_root, agent_id, status="online")
        message = str(payload.get("message") or "").strip()
        if not message:
            raise ValueError("Message is required.")
        actor_id = str(agent.get("agent_id") or agent_id)
        agent_meeting_id = clean_lobby_text(agent.get("meeting_id"), limit=128)
        identity = ActorIdentity(
            agent_id=actor_id,
            display_name=str(agent.get("display_name") or agent.get("agent_id") or agent_id),
            participant_type="live_session",
            meeting_id=agent_meeting_id,
        )
        try:
            ensure_lobby_say_allowed(self.output_root, identity, is_muted=self.lobby.is_muted)
        except GovernedLobbySayRejected as rejected:
            if rejected.category == "muted":
                raise ValueError("This participant is muted by the room host.") from rejected
            raise ValueError(str(rejected)) from rejected

        source_event_id = clean_lobby_text(payload.get("source_event_id"), limit=128)
        with self.lobby.lobby_lock:
            existing_event = existing_live_agent_lobby_reply(
                self.output_root,
                actor_id=actor_id,
                source_event_id=source_event_id,
            )
            if existing_event is not None:
                existing_event = self._redact_existing_smoke_reply(
                    existing_event,
                    actor_id=actor_id,
                    source_event_id=source_event_id,
                )
                updated_agent = heartbeat_live_agent(
                    self.output_root,
                    actor_id,
                    status="online",
                    metadata={
                        "last_error": "",
                        "last_reply_at": existing_event.get("created_at") or datetime.now(UTC).isoformat(),
                        "last_observed_event_id": source_event_id,
                    },
                )
                return {
                    "agent": updated_agent,
                    "event": existing_event,
                    "events": read_lobby(self.output_root),
                }

            flow_metadata = live_agent_lobby_flow_metadata(payload)
            if agent_meeting_id:
                flow_metadata["flow_meeting_id"] = agent_meeting_id
            conflict = flow_turn_conflict(
                self.output_root,
                actor_id=actor_id,
                source_event_id=source_event_id,
                flow_id=str(flow_metadata.get("flow_id") or ""),
                flow_action=str(flow_metadata.get("flow_action") or ""),
                meeting_id=str(flow_metadata.get("flow_meeting_id") or ""),
                message=message,
            )
            if conflict:
                updated_agent = heartbeat_live_agent(
                    self.output_root,
                    actor_id,
                    status="online",
                    metadata={"last_observed_event_id": source_event_id},
                )
                return {
                    "status": conflict,
                    "agent": updated_agent,
                    "events": read_lobby(self.output_root),
                }

            try:
                event = governed_lobby_say(
                    self.output_root,
                    identity=identity,
                    payload={
                        "kind": payload.get("kind") or "message",
                        "message": self.lobby.smoke_reply_message(source_event_id, message),
                        "source_event_id": source_event_id,
                        "auto_chain_depth": payload.get("auto_chain_depth") or 0,
                        **flow_metadata,
                    },
                    append_lobby_event=self.lobby.append_lobby_event,
                    public_lobby_allows_room_scope=self.lobby.public_lobby_allows_room_scope,
                    is_muted=self.lobby.is_muted,
                    policy_already_checked=True,
                    side="other-agent",
                    live_agent_endpoint=True,
                    allow_flow_metadata=True,
                    allowed_kinds=LOBBY_KINDS,
                )
            except GovernedLobbySayRejected as rejected:
                raise ValueError(str(rejected)) from rejected

            reply_metadata: dict[str, object] = {
                "last_error": "",
                "last_reply_at": event.get("created_at") or datetime.now(UTC).isoformat(),
            }
            event_source_id = clean_lobby_text(event.get("source_event_id"), limit=128)
            if event_source_id:
                reply_metadata["last_observed_event_id"] = event_source_id
            updated_agent = heartbeat_live_agent(
                self.output_root,
                actor_id,
                status="online",
                metadata=reply_metadata,
            )
            return {
                "agent": updated_agent,
                "event": event,
                "events": read_lobby(self.output_root),
            }

    def _redact_existing_smoke_reply(
        self,
        existing_event: dict[str, object],
        *,
        actor_id: str,
        source_event_id: str,
    ) -> dict[str, object]:
        if (
            not source_event_id
            or not self.lobby.is_smoke_source_redacted(source_event_id)
            or existing_event.get("message") == self.lobby.smoke_reply_redaction
        ):
            return existing_event
        self.lobby.redact_smoke_events(self.output_root, [source_event_id])
        return existing_live_agent_lobby_reply(
            self.output_root,
            actor_id=actor_id,
            source_event_id=source_event_id,
        ) or existing_event


def flow_turn_conflict(
    output_root: Path,
    *,
    actor_id: str,
    source_event_id: str,
    flow_id: str,
    flow_action: str,
    meeting_id: str,
    message: str,
) -> str:
    """Reject stale or duplicate Play-mode speech without hiding other turns."""

    if not flow_id or flow_action not in FLOW_SPEAKING_ACTIONS:
        return ""
    events = read_lobby(output_root, meeting_id=meeting_id)
    flow_policy = ""
    for event in events:
        if str(event.get("flow_id") or "") == flow_id and str(event.get("flow_event_type") or "") == "started":
            flow_policy = str(event.get("flow_policy") or "")
    normalized_message = " ".join(str(message or "").split()).casefold()
    last_speaking: dict[str, object] | None = None
    for event in reversed(events):
        if str(event.get("flow_id") or "") == flow_id and str(event.get("flow_action") or "") in FLOW_SPEAKING_ACTIONS:
            last_speaking = event
            break
    if (
        last_speaking is not None
        and normalized_message
        and str(last_speaking.get("actor_id") or "") != actor_id
        and " ".join(str(last_speaking.get("message") or "").split()).casefold() == normalized_message
    ):
        return "duplicate_flow_message"
    if flow_policy not in {"turn_based_floor", "natural", "round_robin"}:
        return ""
    seen_source = not source_event_id
    for event in events:
        if not seen_source:
            if str(event.get("id") or "") == source_event_id:
                seen_source = True
            continue
        if str(event.get("flow_id") or "") != flow_id:
            continue
        if str(event.get("flow_action") or "") not in FLOW_SPEAKING_ACTIONS:
            continue
        if str(event.get("actor_id") or "") == actor_id:
            continue
        return "turn_conflict"
    return ""


def live_agent_lobby_flow_metadata(payload: dict[str, object]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for key in (
        "target_agent_id",
        "flow_id",
        "flow_meeting_id",
        "flow_action",
        "flow_reason",
        "flow_runtime_mode",
        "flow_turn_delivery_ms",
        "flow_provider_invocation_ms",
        "flow_reply_post_ms",
    ):
        if key in payload:
            metadata[key] = payload.get(key)
    if "flow_reply_post_ms" not in metadata and payload.get("flow_reply_post_started_at"):
        metadata["flow_reply_post_ms"] = flow_reply_post_elapsed_ms(
            payload.get("flow_reply_post_started_at")
        )
    return metadata


def flow_reply_post_elapsed_ms(value: object) -> int:
    text = clean_lobby_text(value, limit=128)
    if not text:
        return 0
    try:
        started_at = datetime.fromisoformat(text)
    except ValueError:
        return 0
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    return max(
        0,
        int(round((datetime.now(UTC) - started_at.astimezone(UTC)).total_seconds() * 1000)),
    )


def existing_live_agent_lobby_reply(
    output_root: Path,
    *,
    actor_id: str,
    source_event_id: str,
) -> dict[str, object] | None:
    if not source_event_id:
        return None
    for event in reversed(read_lobby(output_root, limit=None)):
        if str(event.get("actor_id") or "") != actor_id:
            continue
        if clean_lobby_text(event.get("source_event_id"), limit=128) != source_event_id:
            continue
        if event.get("live_agent_endpoint") is not True:
            continue
        return event
    return None
